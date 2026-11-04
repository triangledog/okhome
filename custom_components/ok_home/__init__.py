"""OK Hjem integration setup."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.httpx_client import get_async_client

from .api import AuthConfig, AuthState, MemoryAuthStore, OKHomeClient
from .api.auth import OKHomeAuth
from .const import (
    ATTR_CONFIG_ENTRY_ID,
    CONF_DEVICE_IDENTIFICATION,
    CONF_INSTALLATION_ID,
    DOMAIN,
    SERVICE_GET_FORECAST,
    SERVICE_GET_PRICES,
)
from .coordinator import OKHomeDataUpdateCoordinator

PLATFORMS = [Platform.SENSOR]


@dataclass(slots=True)
class OKHomeRuntimeData:
    """Runtime objects owned by one config entry."""

    auth: OKHomeAuth
    client: OKHomeClient
    coordinator: OKHomeDataUpdateCoordinator
    store: MemoryAuthStore


OKHomeConfigEntry = ConfigEntry[OKHomeRuntimeData]


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Register response-only price actions."""
    del config

    def coordinator_for(call: ServiceCall) -> OKHomeDataUpdateCoordinator:
        entry_id = call.data[ATTR_CONFIG_ENTRY_ID]
        entry = hass.config_entries.async_get_entry(entry_id)
        runtime_data = cast(
            OKHomeRuntimeData | None, getattr(entry, "runtime_data", None)
        ) if entry is not None else None
        if (
            entry is None
            or entry.domain != DOMAIN
            or runtime_data is None
        ):
            raise ServiceValidationError("OK Hjem config entry is not loaded")
        return runtime_data.coordinator

    async def get_prices(call: ServiceCall) -> dict[str, Any]:
        coordinator = coordinator_for(call)
        return {
            "prices": [
                {
                    "start": item.start.isoformat(),
                    "end": item.end.isoformat(),
                    "price": item.price,
                    "unit": "DKK/kWh",
                }
                for item in coordinator.known_intervals()
            ]
        }

    async def get_forecast(call: ServiceCall) -> dict[str, Any]:
        coordinator = coordinator_for(call)
        forecast = coordinator.data.forecast_prices if coordinator.data else None
        return {
            "issue_date": forecast.issue_date.isoformat()
            if forecast and forecast.issue_date
            else None,
            "prices": [
                {
                    "start": item.valid_from.isoformat(),
                    "end": item.valid_to.isoformat(),
                    "price": item.total_price,
                    "unit": "DKK/kWh",
                }
                for item in forecast.prices
            ]
            if forecast
            else [],
        }

    schema = vol.Schema({vol.Required(ATTR_CONFIG_ENTRY_ID): cv.string})
    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_PRICES,
        get_prices,
        schema=schema,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_FORECAST,
        get_forecast,
        schema=schema,
        supports_response=SupportsResponse.ONLY,
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: OKHomeConfigEntry) -> bool:
    """Set up an OK Hjem account from a config entry."""
    state = AuthState(
        installation_id=entry.data[CONF_INSTALLATION_ID],
        device_identification=entry.data[CONF_DEVICE_IDENTIFICATION],
    )
    store = MemoryAuthStore(state)
    http = get_async_client(hass)
    auth = OKHomeAuth(AuthConfig.for_app(), store, http_client=http)
    client = OKHomeClient(auth, http_client=http)
    coordinator = OKHomeDataUpdateCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    coordinator.start_interval_updates()
    entry.runtime_data = OKHomeRuntimeData(auth, client, coordinator, store)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: OKHomeConfigEntry) -> bool:
    """Unload an OK Hjem config entry."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    await entry.runtime_data.coordinator.async_shutdown()
    return True
