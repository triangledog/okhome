"""Data coordinator for OK Hjem."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

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
    API_UPDATE_INTERVAL,
    CONF_NET_AREA,
    CONF_POSTAL_CODE,
    CONF_PRODUCT_ID,
    DOMAIN,
)

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PriceInterval:
    """A normalized known-price interval."""

    start: datetime
    end: datetime
    price: float


class OKHomeDataUpdateCoordinator(DataUpdateCoordinator[FuturePricesForecast]):
    """Fetch prices hourly and move sensors locally at quarter boundaries."""

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
            update_interval=API_UPDATE_INTERVAL,
        )
        self.client = client
        self._interval_unsub: Callable[[], None] | None = None

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
        """Start local listener updates at each quarter boundary."""
        if self._interval_unsub is None:
            self._schedule_next_interval(dt_util.utcnow())

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

    @override
    async def async_shutdown(self) -> None:
        if self._interval_unsub is not None:
            self._interval_unsub()
            self._interval_unsub = None
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
