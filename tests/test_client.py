"""Tests for electricity endpoint behavior and model parsing."""

from datetime import timedelta

import httpx
import pytest

from okhome.auth import StaticTokenAuth
from okhome.client import OKHomeClient


def price_payload() -> dict[str, object]:
    """Return a minimal documented v3 response with quarter-hour data."""
    hour = {
        "hourIndex": 0,
        "distributionPrice": 0.8,
        "tax": 0.9,
        "electricityPrice": 1.0,
        "totalPrice": 2.7,
        "priceState": 0,
        "validFrom": "2026-10-25T00:00:00Z",
        "validTo": "2026-10-25T01:00:00Z",
        "priceLevel": 1,
        "distributionPricePercentage": 29.6,
        "taxPercentage": 33.3,
        "electricityPricePercentage": 37.1,
        "quarterHourPrices": [
            {
                "quarterHourIndex": 0,
                "validFrom": "2026-10-25T00:00:00Z",
                "validTo": "2026-10-25T00:15:00Z",
                "totalPrice": 2.6,
                "priceState": 0,
            }
        ],
    }
    return {
        "futurePrices": {
            "prices": [hour],
            "averagePrice": 2.7,
            "minPrice": 2.7,
            "maxPrice": 2.7,
            "productName": "Test product",
        },
        "forecastPrices": {
            "prices": [hour],
            "averagePrice": 2.8,
            "minPrice": 2.0,
            "maxPrice": 3.0,
            "issueDate": "2026-10-24T12:00:00Z",
        },
        "movingAverageHours": [],
    }


@pytest.mark.asyncio
async def test_products_do_not_require_auth() -> None:
    """The product endpoint is publicly callable despite required OpenAPI headers."""
    def handler(request: httpx.Request) -> httpx.Response:
        assert "X-App-Request-Token" not in request.headers
        return httpx.Response(
            200,
            json=[{"futurePriceProductId": 12, "productName": "OK El", "productText": "..."}],
        )

    http = httpx.AsyncClient(
        base_url="https://electricity-home.okcloud.dk",
        transport=httpx.MockTransport(handler),
    )
    client = OKHomeClient(http_client=http)
    products = await client.get_products()
    assert products[0].product_id == 12
    await http.aclose()


@pytest.mark.asyncio
async def test_get_future_prices_and_timezone() -> None:
    """v3 parameters and offset-aware Copenhagen conversion are preserved."""
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-App-Device-Identification"] == "device"
        assert request.headers["X-App-Request-Token"] == "token"
        assert request.url.params["NetArea"] == "DK2"
        assert request.url.params["FuturePriceProductId"] == "12"
        assert request.url.params["PostalCode"] == "2100"
        return httpx.Response(200, json=price_payload())

    http = httpx.AsyncClient(
        base_url="https://electricity-home.okcloud.dk",
        transport=httpx.MockTransport(handler),
    )
    client = OKHomeClient(StaticTokenAuth("device", "token"), http_client=http)
    result = await client.get_future_prices(net_area="DK2", product_id=12, postal_code=2100)
    price = result.future_prices.prices[0]

    assert price.total_price == 2.7
    assert price.valid_to - price.valid_from == timedelta(hours=1)
    assert price.local_valid_from.utcoffset() == timedelta(hours=2)
    assert price.quarter_hour_prices[0].total_price == 2.6
    assert price.quarter_hour_prices[0].local_valid_from.utcoffset() == timedelta(hours=2)
    await http.aclose()


@pytest.mark.asyncio
async def test_first_active_metering_point_selects_app_parameters() -> None:
    """The metering-point product, net area, and postcode feed v3 unchanged."""
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path.endswith("GetMeteringPoints"):
            return httpx.Response(
                200,
                json={
                    "meteringPoints": [
                        {
                            "meteringPointIdentification": "571...",
                            "address": {
                                "addressStreetName": "Testvej",
                                "addressHouseNumber": "1",
                                "addressPostalCode": 8000,
                                "netArea": "DK1",
                            },
                            "agreementDuration": {"isActive": True},
                            "electricityProduct": {
                                "electricityProductId": 7,
                                "electricityProductName": "Variabel",
                                "isVariableProduct": True,
                            },
                        }
                    ]
                },
            )
        assert request.url.params["NetArea"] == "DK1"
        assert request.url.params["FuturePriceProductId"] == "7"
        assert request.url.params["PostalCode"] == "8000"
        return httpx.Response(200, json=price_payload())

    http = httpx.AsyncClient(
        base_url="https://electricity-home.okcloud.dk",
        transport=httpx.MockTransport(handler),
    )
    client = OKHomeClient(StaticTokenAuth("device", "token"), http_client=http)
    result = await client.get_prices_for_first_active_metering_point()

    assert result.future_prices.product_name == "Test product"
    assert paths == [
        "/api/v2/UserProfile/GetMeteringPoints",
        "/api/v3/Prices/GetFuturePrices",
    ]
    await http.aclose()
