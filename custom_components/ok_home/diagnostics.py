"""Diagnostics for OK Hjem."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.redact import async_redact_data

from . import OKHomeConfigEntry
from .const import (
    CONF_ADDRESS,
    CONF_DEVICE_IDENTIFICATION,
    CONF_INSTALLATION_ID,
    CONF_METERING_POINT,
    CONF_NET_AREA,
    CONF_POSTAL_CODE,
    CONF_PROFILE_NUMBER,
)

TO_REDACT = {
    "email",
    CONF_ADDRESS,
    CONF_DEVICE_IDENTIFICATION,
    CONF_INSTALLATION_ID,
    CONF_METERING_POINT,
    CONF_NET_AREA,
    CONF_POSTAL_CODE,
    CONF_PROFILE_NUMBER,
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: OKHomeConfigEntry
) -> dict[str, Any]:
    """Return redacted configuration and non-sensitive coordinator status."""
    del hass
    coordinator = entry.runtime_data.coordinator
    return {
        "config": async_redact_data(dict(entry.data), TO_REDACT),
        "last_update_success": coordinator.last_update_success,
        "known_interval_count": len(coordinator.known_intervals()),
        "forecast_interval_count": len(coordinator.data.forecast_prices.prices)
        if coordinator.data
        else 0,
    }
