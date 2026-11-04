"""Tests for Home Assistant price interval coordination."""

from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace

from homeassistant.components import persistent_notification

from custom_components.ok_home.api.models import (
    FuturePricesForecast,
    HourlyPrice,
    PriceSeries,
    QuarterHourlyPrice,
)
from custom_components.ok_home.coordinator import (
    COPENHAGEN,
    OKHomeDataUpdateCoordinator,
    next_publication_refresh,
)


def quarter(start: datetime, price: float, index: int = 0) -> QuarterHourlyPrice:
    """Create a quarter-hour fixture."""
    return QuarterHourlyPrice(
        index=index,
        valid_from=start,
        valid_to=start + timedelta(minutes=15),
        total_price=price,
        price_state=0,
    )


def hour(
    start: datetime,
    price: float,
    quarters: tuple[QuarterHourlyPrice, ...] = (),
) -> HourlyPrice:
    """Create an hourly fixture with optional quarters."""
    return HourlyPrice(
        index=0,
        valid_from=start,
        valid_to=start + timedelta(hours=1),
        distribution_price=0.8,
        tax=0.01,
        electricity_price=price - 0.81,
        total_price=price,
        price_state=0,
        price_level=0,
        price_message=None,
        distribution_price_percentage=0,
        tax_percentage=0,
        electricity_price_percentage=0,
        quarter_hour_prices=quarters,
    )


def coordinator_with(hours: tuple[HourlyPrice, ...]) -> OKHomeDataUpdateCoordinator:
    """Create a coordinator shell for pure interval methods."""
    series = PriceSeries(hours, 2.0, 1.0, 3.0)
    coordinator = object.__new__(OKHomeDataUpdateCoordinator)
    coordinator.data = FuturePricesForecast(series, PriceSeries((), 0, 0, 0), ())
    return coordinator


def test_known_intervals_prefer_quarters_and_fall_back_to_hour() -> None:
    """Only API-supplied quarters are flattened; absent quarters retain the hour."""
    start = datetime(2026, 11, 5, tzinfo=UTC)
    quarters = tuple(
        quarter(start + timedelta(minutes=15 * index), 2.0 + index / 10, index)
        for index in range(4)
    )
    coordinator = coordinator_with(
        (hour(start, 2.15, quarters), hour(start + timedelta(hours=1), 2.5))
    )

    intervals = coordinator.known_intervals()
    assert len(intervals) == 5
    assert [item.price for item in intervals] == [2.0, 2.1, 2.2, 2.3, 2.5]
    assert intervals[-1].end - intervals[-1].start == timedelta(hours=1)


def test_current_interval_uses_half_open_boundaries() -> None:
    """At an exact boundary, the new quarter is current rather than the previous one."""
    start = datetime(2026, 11, 5, tzinfo=UTC)
    coordinator = coordinator_with(
        (hour(start, 2.0, (quarter(start, 2.0), quarter(start + timedelta(minutes=15), 2.1))),)
    )

    assert coordinator.current_interval_index(start + timedelta(minutes=14, seconds=59)) == 0
    assert coordinator.current_interval_index(start + timedelta(minutes=15)) == 1
    assert coordinator.current_interval_index(start + timedelta(minutes=30)) is None


def test_repeated_dst_hour_is_selected_by_offset() -> None:
    """Repeated Copenhagen wall times remain distinct instants."""
    summer_offset = timezone(timedelta(hours=2))
    winter_offset = timezone(timedelta(hours=1))
    first = datetime(2026, 10, 25, 2, 0, tzinfo=summer_offset)
    second = datetime(2026, 10, 25, 2, 0, tzinfo=winter_offset)
    coordinator = coordinator_with(
        (
            hour(first, 2.0, (quarter(first, 2.0),)),
            hour(second, 2.5, (quarter(second, 2.5),)),
        )
    )

    assert coordinator.current_interval_index(first + timedelta(minutes=5)) == 0
    assert coordinator.current_interval_index(second + timedelta(minutes=5)) == 1


def test_next_publication_refresh_uses_copenhagen_time() -> None:
    """Daily refresh follows local publication time rather than a fixed UTC hour."""
    before_publication = datetime(2026, 1, 5, 11, 0, tzinfo=UTC)
    winter_refresh = next_publication_refresh(before_publication)
    assert winter_refresh.astimezone(COPENHAGEN).isoformat() == "2026-01-05T13:05:00+01:00"

    after_publication_before_dst = datetime(2026, 3, 28, 14, 0, tzinfo=UTC)
    summer_refresh = next_publication_refresh(after_publication_before_dst)
    assert summer_refresh.astimezone(COPENHAGEN).isoformat() == "2026-03-29T13:05:00+02:00"


def test_tomorrow_prices_are_detected_in_copenhagen_date() -> None:
    """Retry decisions use the local delivery date represented by known intervals."""
    now = datetime(2026, 11, 5, 12, 30, tzinfo=UTC)
    tomorrow_start = datetime(2026, 11, 5, 23, 0, tzinfo=UTC)
    coordinator = coordinator_with((hour(tomorrow_start, 2.0),))

    assert coordinator.has_tomorrow_prices(now)


def test_price_failure_notification_is_entry_specific(monkeypatch) -> None:
    """A failed location gets a clear notification that can later be dismissed."""
    coordinator = object.__new__(OKHomeDataUpdateCoordinator)
    coordinator.hass = object()
    coordinator.config_entry = SimpleNamespace(entry_id="entry-123", title="Testvej 12")
    created = []
    dismissed = []
    monkeypatch.setattr(
        persistent_notification,
        "async_create",
        lambda hass, message, title, notification_id: created.append(
            (hass, message, title, notification_id)
        ),
    )
    monkeypatch.setattr(
        persistent_notification,
        "async_dismiss",
        lambda hass, notification_id: dismissed.append((hass, notification_id)),
    )

    coordinator._create_price_notification()
    coordinator._dismiss_price_notification()

    assert "Charging plans and automations" in created[0][1]
    assert created[0][2] == "OK Hjem prices unavailable: Testvej 12"
    assert created[0][3] == "ok_home_entry-123_price_refresh_failed"
    assert dismissed == [(coordinator.hass, created[0][3])]
