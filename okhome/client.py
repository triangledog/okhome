"""Async client for the OK Hjem electricity BFF."""

from __future__ import annotations

import uuid
from typing import Any

import httpx

from okhome.auth import RequestTokenProvider
from okhome.exceptions import APIError, AuthenticationError, NetworkError, ProtocolError
from okhome.models import FuturePricesForecast, MeteringPoint, PriceProduct

ELECTRICITY_URL = "https://electricity-home.okcloud.dk"


class OKHomeClient:
    """Home Assistant-friendly async electricity API client."""

    def __init__(
        self,
        auth: RequestTokenProvider | None = None,
        *,
        caller: str = "okhome-python",
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._auth = auth
        self._caller = caller
        self._http = http_client or httpx.AsyncClient(base_url=ELECTRICITY_URL, timeout=20.0)
        self._owns_http = http_client is None

    async def _get(
        self,
        path: str,
        *,
        params: dict[str, str | int | float] | None = None,
        authenticated: bool = True,
    ) -> Any:
        headers = {"caller": self._caller, "correlationId": str(uuid.uuid4())}
        attempts = 2 if authenticated else 1
        for attempt in range(attempts):
            if not authenticated:
                try:
                    response = await self._http.get(
                        f"{ELECTRICITY_URL}{path}", params=params, headers=headers
                    )
                except httpx.RequestError as exc:
                    raise NetworkError(f"GET {path} failed") from exc
                break
            if self._auth is None:
                raise AuthenticationError("This endpoint requires an authentication provider")
            token = await self._auth.get_token(force=attempt == 1)
            device = self._auth.device_identification
            if not device:
                raise AuthenticationError("No server-issued device identification is available")
            headers.update(
                {
                    "X-App-Device-Identification": device,
                    "X-App-Request-Token": token,
                }
            )
            try:
                response = await self._http.get(
                    f"{ELECTRICITY_URL}{path}",
                    params=params,
                    headers=headers,
                )
            except httpx.RequestError as exc:
                raise NetworkError(f"GET {path} failed") from exc
            if response.status_code not in (401, 403):
                break
        if response.status_code in (401, 403):
            raise AuthenticationError(f"Authentication rejected with HTTP {response.status_code}")
        if not response.is_success:
            raise APIError(response.status_code, response.text[:500] or response.reason_phrase)
        try:
            return response.json()
        except ValueError as exc:
            raise ProtocolError("API returned invalid JSON") from exc

    async def get_products(self) -> tuple[PriceProduct, ...]:
        """Return products from the endpoint verified to allow anonymous access."""
        payload = await self._get(
            "/api/v1/Prices/GetProductsForFuturePrices", authenticated=False
        )
        if not isinstance(payload, list):
            raise ProtocolError("Product response must be a list")
        if not all(isinstance(item, dict) for item in payload):
            raise ProtocolError("Every product must be an object")
        return tuple(PriceProduct.from_dict(item) for item in payload)

    async def get_metering_points(self) -> tuple[MeteringPoint, ...]:
        """Return metering points associated with the logged-in device/account."""
        payload = await self._get("/api/v2/UserProfile/GetMeteringPoints")
        points = payload.get("meteringPoints") if isinstance(payload, dict) else None
        if points is None:
            return ()
        if not isinstance(points, list):
            raise ProtocolError("meteringPoints must be a list or null")
        if not all(isinstance(item, dict) for item in points):
            raise ProtocolError("Every metering point must be an object")
        return tuple(MeteringPoint.from_dict(item) for item in points)

    async def get_net_area(
        self,
        *,
        street: str,
        house_number: str,
        postal_code: int,
        floor: str | None = None,
        suite: str | None = None,
    ) -> str:
        """Resolve a net area from address fields."""
        params: dict[str, str | int | float] = {
            "StreetName": street,
            "HouseNumber": house_number,
            "PostalCode": postal_code,
        }
        if floor:
            params["Floor"] = floor
        if suite:
            params["Suite"] = suite
        payload = await self._get("/api/v1/Prices/GetNetAreaFromAdress", params=params)
        net_area = payload.get("netArea") if isinstance(payload, dict) else None
        if not isinstance(net_area, str):
            raise ProtocolError("Net-area response has no netArea")
        return net_area

    async def get_future_prices(
        self,
        *,
        net_area: str,
        product_id: int,
        postal_code: int,
        custom_price_limit_low: float | None = None,
        custom_price_limit_high: float | None = None,
    ) -> FuturePricesForecast:
        """Return known future prices and forecast prices from API v3."""
        params: dict[str, str | int | float] = {
            "NetArea": net_area,
            "FuturePriceProductId": product_id,
            "PostalCode": postal_code,
        }
        if custom_price_limit_low is not None:
            params["CustomPriceLimitLow"] = custom_price_limit_low
        if custom_price_limit_high is not None:
            params["CustomPriceLimitHigh"] = custom_price_limit_high
        payload = await self._get("/api/v3/Prices/GetFuturePrices", params=params)
        if not isinstance(payload, dict):
            raise ProtocolError("Future-price response must be an object")
        return FuturePricesForecast.from_dict(payload)

    async def get_prices_for_first_active_metering_point(self) -> FuturePricesForecast:
        """Resolve and fetch prices using the first complete active metering point."""
        points = await self.get_metering_points()
        for point in points:
            if (
                point.agreement_active
                and point.address.net_area
                and point.product.product_id is not None
                and point.address.postal_code
            ):
                return await self.get_future_prices(
                    net_area=point.address.net_area,
                    product_id=point.product.product_id,
                    postal_code=point.address.postal_code,
                )
        raise ProtocolError("No active metering point has net area, product ID, and postal code")

    async def aclose(self) -> None:
        """Close the internally owned HTTP client."""
        if self._owns_http:
            await self._http.aclose()

    async def __aenter__(self) -> OKHomeClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()
