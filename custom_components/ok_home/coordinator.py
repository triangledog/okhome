"""Data coordinator for OK Hjem."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from typing_extensions import override

from .api import FuturePricesForecast, OKHomeClient
from .api.exceptions import APIError, AuthenticationError, NetworkError, ProtocolError
from .api.models import HourlyPrice
from .const import (
    CONF_NET_AREA,
    CONF_POSTAL_CODE,
    CONF_PRODUCT_ID,
    DOMAIN,
    PRICE_MAX_RETRIES,
    PRICE_PUBLICATION_HOUR,
    PRICE_PUBLICATION_MINUTE,
    PRICE_RETRY_MINUTES,
)

LOGGER = logging.getLogger(__name__)
COPENHAGEN = ZoneInfo("Europe/Copenhagen")
PRICE_NOTIFICATION_SUFFIX = "price_refresh_failed"


def next_publication_refresh(now: datetime, *, skip_today: bool = False) -> datetime:
    """Return the next 13:05 Copenhagen publication refresh in UTC."""
    local_now = now.astimezone(COPENHAGEN)
    target = datetime.combine(
        local_now.date(),
        time(PRICE_PUBLICATION_HOUR, PRICE_PUBLICATION_MINUTE),
        tzinfo=COPENHAGEN,
    )
    if skip_today or target <= local_now:
        target += timedelta(days=1)
    return target.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class PriceInterval:
    """A normalized known-price interval."""

    start: datetime
    end: datetime
    price: float


class OKHomeDataUpdateCoordinator(DataUpdateCoordinator[FuturePricesForecast]):
    """Fetch after daily publication and move sensors locally each quarter."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        client: OKHomeClient,
    ) -> None:
        super().__init__(
            hass,
            LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
        )
        self.client = client
        self._interval_unsub: Callable[[], None] | None = None
        self._refresh_unsub: Callable[[], None] | None = None
        self._refresh_retries = 0

    @override
    async def _async_update_data(self) -> FuturePricesForecast:
        try:
            return await self.client.get_future_prices(
                net_area=self.config_entry.data[CONF_NET_AREA],
                product_id=self.config_entry.data[CONF_PRODUCT_ID],
                postal_code=self.config_entry.data[CONF_POSTAL_CODE],
            )
        except AuthenticationError as exc:
            raise ConfigEntryAuthFailed("OK Hjem authentication expired") from exc
        except (APIError, NetworkError, ProtocolError) as exc:
            raise UpdateFailed(f"Unable to update OK Hjem prices: {exc}") from exc

    def start_interval_updates(self) -> None:
        """Start local quarter updates and the daily API refresh schedule."""
        now = dt_util.utcnow()
        if self._interval_unsub is None:
            self._schedule_next_interval(now)
        if self._refresh_unsub is None:
            self._schedule_refresh_after_startup(now)

    def _schedule_next_interval(self, now: datetime) -> None:
        candidate = now + timedelta(minutes=15)
        next_minute = candidate.minute // 15 * 15
        next_run = candidate.replace(minute=next_minute, second=1, microsecond=0)
        self._interval_unsub = async_track_point_in_utc_time(
            self.hass, self._handle_interval, next_run
        )

    async def _handle_interval(self, now: datetime) -> None:
        self._interval_unsub = None
        self.async_update_listeners()
        self._schedule_next_interval(now)

    def has_tomorrow_prices(self, now: datetime | None = None) -> bool:
        """Return whether known intervals include the next Copenhagen date."""
        local_now = (now or dt_util.utcnow()).astimezone(COPENHAGEN)
        tomorrow = local_now.date() + timedelta(days=1)
        return any(
            item.start.astimezone(COPENHAGEN).date() >= tomorrow
            for item in self.known_intervals()
        )

    def _schedule_refresh_after_startup(self, now: datetime) -> None:
        local_now = now.astimezone(COPENHAGEN)
        publication = time(PRICE_PUBLICATION_HOUR, PRICE_PUBLICATION_MINUTE)
        if self.has_tomorrow_prices(now):
            self._dismiss_price_notification()
            self._schedule_daily_refresh(now, skip_today=True)
        elif local_now.time() >= publication:
            self._schedule_retry(now)
        else:
            self._schedule_daily_refresh(now)

    def _schedule_daily_refresh(self, now: datetime, *, skip_today: bool = False) -> None:
        self._refresh_retries = 0
        self._schedule_refresh_at(next_publication_refresh(now, skip_today=skip_today))

    def _schedule_retry(self, now: datetime) -> None:
        self._refresh_retries += 1
        self._schedule_refresh_at(now + timedelta(minutes=PRICE_RETRY_MINUTES))

    def _schedule_refresh_at(self, when: datetime) -> None:
        self._refresh_unsub = async_track_point_in_utc_time(
            self.hass, self._handle_price_refresh, when
        )

    async def _handle_price_refresh(self, now: datetime) -> None:
        self._refresh_unsub = None
        await self.async_request_refresh()
        if self.has_tomorrow_prices(now):
            self._dismiss_price_notification()
            self._schedule_daily_refresh(now)
        elif self._refresh_retries < PRICE_MAX_RETRIES:
            self._schedule_retry(now)
        else:
            LOGGER.warning(
                "Tomorrow's OK Hjem prices are still unavailable after %s retries",
                PRICE_MAX_RETRIES,
            )
            self._create_price_notification()
            self._schedule_daily_refresh(now)

    @property
    def _price_notification_id(self) -> str:
        return f"{DOMAIN}_{self.config_entry.entry_id}_{PRICE_NOTIFICATION_SUFFIX}"

    def _create_price_notification(self) -> None:
        persistent_notification.async_create(
            self.hass,
            (
                "OK Hjem could not retrieve tomorrow's electricity prices after "
                f"{PRICE_MAX_RETRIES} retries. Charging plans and automations may use "
                "stale or unavailable prices. Check the integration and Home Assistant logs."
            ),
            title=f"OK Hjem prices unavailable: {self.config_entry.title}",
            notification_id=self._price_notification_id,
        )

    def _dismiss_price_notification(self) -> None:
        persistent_notification.async_dismiss(self.hass, self._price_notification_id)

    @override
    async def async_shutdown(self) -> None:
        if self._interval_unsub is not None:
            self._interval_unsub()
            self._interval_unsub = None
        if self._refresh_unsub is not None:
            self._refresh_unsub()
            self._refresh_unsub = None
        await super().async_shutdown()

    def known_intervals(self) -> tuple[PriceInterval, ...]:
        """Flatten known quarter-hour prices, falling back to hourly entries."""
        if self.data is None:
            return ()
        intervals: list[PriceInterval] = []
        for hour in self.data.future_prices.prices:
            if hour.quarter_hour_prices:
                intervals.extend(
                    PriceInterval(item.valid_from, item.valid_to, item.total_price)
                    for item in hour.quarter_hour_prices
                )
            else:
                intervals.append(
                    PriceInterval(hour.valid_from, hour.valid_to, hour.total_price)
                )
        return tuple(sorted(intervals, key=lambda item: item.start))

    def current_interval_index(self, now: datetime | None = None) -> int | None:
        """Return the index containing the current instant."""
        current = now or dt_util.utcnow()
        for index, item in enumerate(self.known_intervals()):
            if item.start <= current < item.end:
                return index
        return None

    def relative_interval(self, offset: int) -> PriceInterval | None:
        """Return current, previous, or next known interval."""
        intervals = self.known_intervals()
        index = self.current_interval_index()
        if index is None or not 0 <= index + offset < len(intervals):
            return None
        return intervals[index + offset]

    def current_hour(self, now: datetime | None = None) -> HourlyPrice | None:
        """Return the hourly component record containing the current instant."""
        if self.data is None:
            return None
        current = now or dt_util.utcnow()
        return next(
            (
                item
                for item in self.data.future_prices.prices
                if item.valid_from <= current < item.valid_to
            ),
            None,
        )
