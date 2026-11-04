"""Config flow for OK Hjem."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.helpers.httpx_client import get_async_client

from .api import AuthConfig, AuthState, MemoryAuthStore, MeteringPoint, OKHomeClient
from .api.auth import OKHomeAuth
from .api.exceptions import (
    APIError,
    AuthenticationError,
    NetworkError,
    OKHomeError,
    ProtocolError,
)
from .const import (
    CONF_ADDRESS,
    CONF_DEVICE_IDENTIFICATION,
    CONF_INSTALLATION_ID,
    CONF_METERING_POINT,
    CONF_NET_AREA,
    CONF_POSTAL_CODE,
    CONF_PRODUCT_ID,
    CONF_PRODUCT_NAME,
    CONF_PROFILE_NUMBER,
    DOMAIN,
)

LOGIN_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


class OKHomeConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle OK Hjem configuration and reauthentication."""

    VERSION = 1

    def __init__(self) -> None:
        self._email = ""
        self._profile_number: int | None = None
        self._store: MemoryAuthStore | None = None
        self._points: dict[str, MeteringPoint] = {}

    async def _login(
        self,
        email: str,
        password: str,
        state: AuthState | None = None,
    ) -> None:
        self._email = email
        self._store = MemoryAuthStore(state)
        http = get_async_client(self.hass)
        auth = OKHomeAuth(AuthConfig.for_app(), self._store, http_client=http)
        payload = await auth.login(email, password)
        profile = payload.get("userProfile")
        profile_number = profile.get("userProfileNumber") if isinstance(profile, dict) else None
        if not isinstance(profile_number, int):
            raise AuthenticationError("Login response has no user profile number")
        self._profile_number = profile_number
        client = OKHomeClient(auth, http_client=http)
        points = await client.get_metering_points()
        self._points = {
            point.identification: point
            for point in points
            if point.identification
            and point.agreement_active
            and point.address.net_area
            and point.product.product_id is not None
            and point.address.postal_code
        }
        if not self._points:
            raise OKHomeError("No complete active metering point was found")

    def _entry_data(self, point: MeteringPoint) -> dict[str, Any]:
        if self._store is None or self._profile_number is None:
            raise RuntimeError("Login flow has no authenticated state")
        state = self._store.state
        if state.device_identification is None or point.identification is None:
            raise RuntimeError("Login flow has incomplete device or metering-point state")
        return {
            CONF_EMAIL: self._email,
            CONF_PROFILE_NUMBER: self._profile_number,
            CONF_INSTALLATION_ID: state.installation_id,
            CONF_DEVICE_IDENTIFICATION: state.device_identification,
            CONF_METERING_POINT: point.identification,
            CONF_ADDRESS: point.address.street_address,
            CONF_NET_AREA: point.address.net_area,
            CONF_POSTAL_CODE: point.address.postal_code,
            CONF_PRODUCT_ID: point.product.product_id,
            CONF_PRODUCT_NAME: point.product.name,
        }

    async def _finish(self, point: MeteringPoint) -> ConfigFlowResult:
        await self.async_set_unique_id(f"{self._profile_number}:{point.identification}")
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=point.address.street_address or point.product.name or self._email,
            data=self._entry_data(point),
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Authenticate and discover active agreements."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                await self._login(user_input[CONF_EMAIL], user_input[CONF_PASSWORD])
            except AuthenticationError:
                errors["base"] = "invalid_auth"
            except (APIError, NetworkError):
                errors["base"] = "cannot_connect"
            except ProtocolError:
                errors["base"] = "unknown"
            except OKHomeError:
                errors["base"] = "no_metering_points"
            else:
                if len(self._points) == 1:
                    return await self._finish(next(iter(self._points.values())))
                return await self.async_step_metering_point()
        return self.async_show_form(step_id="user", data_schema=LOGIN_SCHEMA, errors=errors)

    async def async_step_metering_point(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select one active metering point."""
        if user_input is not None:
            return await self._finish(self._points[user_input[CONF_METERING_POINT]])
        labels = {
            identifier: self._point_label(point)
            for identifier, point in self._points.items()
        }
        return self.async_show_form(
            step_id="metering_point",
            data_schema=vol.Schema({vol.Required(CONF_METERING_POINT): vol.In(labels)}),
        )

    @staticmethod
    def _point_label(point: MeteringPoint) -> str:
        return f"{point.address.street_address} ({point.product.name or 'OK El'})"

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Start reauthentication for an existing device association."""
        self._email = entry_data[CONF_EMAIL]
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm credentials and update stable device state."""
        errors: dict[str, str] = {}
        reauth_entry = self._get_reauth_entry()
        if user_input is not None:
            state = AuthState(
                installation_id=reauth_entry.data[CONF_INSTALLATION_ID],
                device_identification=reauth_entry.data[CONF_DEVICE_IDENTIFICATION],
            )
            try:
                await self._login(self._email, user_input[CONF_PASSWORD], state)
            except AuthenticationError:
                errors["base"] = "invalid_auth"
            except (APIError, NetworkError):
                errors["base"] = "cannot_connect"
            except (OKHomeError, ProtocolError):
                errors["base"] = "unknown"
            else:
                point = self._points.get(reauth_entry.data[CONF_METERING_POINT])
                if point is None:
                    errors["base"] = "no_metering_points"
                else:
                    return self.async_update_reload_and_abort(
                        reauth_entry,
                        title=point.address.street_address or reauth_entry.title,
                        data_updates=self._entry_data(point),
                    )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            errors=errors,
            description_placeholders={"email": self._email},
        )
