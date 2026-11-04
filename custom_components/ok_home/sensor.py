"""Sensors for OK Hjem electricity prices."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription, SensorStateClass
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from typing_extensions import override

from . import OKHomeConfigEntry
from .const import (
    CONF_ADDRESS,
    CONF_METERING_POINT,
    CONF_PRODUCT_NAME,
    DOMAIN,
    PRICE_UNIT,
)
from .coordinator import OKHomeDataUpdateCoordinator, PriceInterval


@dataclass(frozen=True, kw_only=True)
class OKHomeSensorDescription(SensorEntityDescription):
    """Describe an OK Hjem price sensor."""

    value_fn: Callable[[OKHomeDataUpdateCoordinator], float | None]
    interval_fn: Callable[[OKHomeDataUpdateCoordinator], PriceInterval | None] | None = None


def _relative_value(coordinator: OKHomeDataUpdateCoordinator, offset: int) -> float | None:
    interval = coordinator.relative_interval(offset)
    return interval.price if interval else None


def _hour_value(
    coordinator: OKHomeDataUpdateCoordinator, attribute: str
) -> float | None:
    hour = coordinator.current_hour()
    return getattr(hour, attribute) if hour else None


SENSORS: tuple[OKHomeSensorDescription, ...] = (
    OKHomeSensorDescription(
        key="current_price",
        translation_key="current_price",
        native_unit_of_measurement=PRICE_UNIT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda coordinator: _relative_value(coordinator, 0),
        interval_fn=lambda coordinator: coordinator.relative_interval(0),
    ),
    OKHomeSensorDescription(
        key="previous_price",
        translation_key="previous_price",
        native_unit_of_measurement=PRICE_UNIT,
        suggested_display_precision=2,
        value_fn=lambda coordinator: _relative_value(coordinator, -1),
        interval_fn=lambda coordinator: coordinator.relative_interval(-1),
    ),
    OKHomeSensorDescription(
        key="next_price",
        translation_key="next_price",
        native_unit_of_measurement=PRICE_UNIT,
        suggested_display_precision=2,
        value_fn=lambda coordinator: _relative_value(coordinator, 1),
        interval_fn=lambda coordinator: coordinator.relative_interval(1),
    ),
    OKHomeSensorDescription(
        key="lowest_known_price",
        translation_key="lowest_known_price",
        native_unit_of_measurement=PRICE_UNIT,
        suggested_display_precision=2,
        value_fn=lambda coordinator: min(
            (item.price for item in coordinator.known_intervals()), default=None
        ),
        interval_fn=lambda coordinator: min(
            coordinator.known_intervals(), key=lambda item: item.price, default=None
        ),
    ),
    OKHomeSensorDescription(
        key="highest_known_price",
        translation_key="highest_known_price",
        native_unit_of_measurement=PRICE_UNIT,
        suggested_display_precision=2,
        value_fn=lambda coordinator: max(
            (item.price for item in coordinator.known_intervals()), default=None
        ),
        interval_fn=lambda coordinator: max(
            coordinator.known_intervals(), key=lambda item: item.price, default=None
        ),
    ),
    OKHomeSensorDescription(
        key="known_average_price",
        translation_key="known_average_price",
        native_unit_of_measurement=PRICE_UNIT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda coordinator: coordinator.data.future_prices.average_price
        if coordinator.data
        else None,
        entity_registry_enabled_default=False,
    ),
    OKHomeSensorDescription(
        key="electricity_price",
        translation_key="electricity_price",
        native_unit_of_measurement=PRICE_UNIT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=4,
        value_fn=lambda coordinator: _hour_value(coordinator, "electricity_price"),
    ),
    OKHomeSensorDescription(
        key="distribution_price",
        translation_key="distribution_price",
        native_unit_of_measurement=PRICE_UNIT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=4,
        value_fn=lambda coordinator: _hour_value(coordinator, "distribution_price"),
    ),
    OKHomeSensorDescription(
        key="tax",
        translation_key="tax",
        native_unit_of_measurement=PRICE_UNIT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=4,
        value_fn=lambda coordinator: _hour_value(coordinator, "tax"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OKHomeConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up OK Hjem sensors."""
    del hass
    async_add_entities(
        OKHomePriceSensor(entry, entry.runtime_data.coordinator, description)
        for description in SENSORS
    )


class OKHomePriceSensor(CoordinatorEntity[OKHomeDataUpdateCoordinator], SensorEntity):
    """A price sensor backed by the shared coordinator response."""

    entity_description: OKHomeSensorDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        entry: OKHomeConfigEntry,
        coordinator: OKHomeDataUpdateCoordinator,
        description: OKHomeSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        metering_point = entry.data[CONF_METERING_POINT]
        self._attr_unique_id = f"{metering_point}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, metering_point)},
            manufacturer="OK",
            model=entry.data.get(CONF_PRODUCT_NAME),
            name=entry.data.get(CONF_ADDRESS, entry.title),
        )

    @property
    @override
    def native_value(self) -> float | None:
        """Return the current sensor value."""
        return self.entity_description.value_fn(self.coordinator)

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Expose only the selected interval, never the complete price series."""
        if self.entity_description.interval_fn is None:
            return None
        interval = self.entity_description.interval_fn(self.coordinator)
        if interval is None:
            return None
        return {
            "valid_from": interval.start.isoformat(),
            "valid_to": interval.end.isoformat(),
        }
