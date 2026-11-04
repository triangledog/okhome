"""API-independent data models for OK Hjem electricity data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from okhome.exceptions import ProtocolError

COPENHAGEN = ZoneInfo("Europe/Copenhagen")


def _datetime(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise ProtocolError(f"{field} must be an RFC 3339 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProtocolError(f"Invalid {field}: {value!r}") from exc
    if parsed.tzinfo is None:
        raise ProtocolError(f"{field} has no UTC offset; timezone cannot be inferred safely")
    return parsed


def _float(data: dict[str, Any], field: str) -> float:
    value = data.get(field, 0.0)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ProtocolError(f"{field} must be numeric")
    return float(value)


def _int(data: dict[str, Any], field: str) -> int:
    value = data.get(field, 0)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ProtocolError(f"{field} must be an integer")
    return value


@dataclass(frozen=True, slots=True)
class QuarterHourlyPrice:
    """One quarter-hour price with a total price in DKK/kWh."""

    index: int
    valid_from: datetime
    valid_to: datetime
    total_price: float
    price_state: int

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> QuarterHourlyPrice:
        """Parse the OpenAPI `QuarterHourlyPriceDto` shape."""
        return cls(
            index=_int(data, "quarterHourIndex"),
            valid_from=_datetime(data.get("validFrom"), "validFrom"),
            valid_to=_datetime(data.get("validTo"), "validTo"),
            total_price=_float(data, "totalPrice"),
            price_state=_int(data, "priceState"),
        )

    @property
    def local_valid_from(self) -> datetime:
        """Return the interval start in Europe/Copenhagen."""
        return self.valid_from.astimezone(COPENHAGEN)


@dataclass(frozen=True, slots=True)
class HourlyPrice:
    """One hour with grid tariffs, state duty, and electricity incl. markup."""

    index: int
    valid_from: datetime
    valid_to: datetime
    distribution_price: float
    tax: float
    electricity_price: float
    total_price: float
    price_state: int
    price_level: int
    price_message: str | None
    distribution_price_percentage: float
    tax_percentage: float
    electricity_price_percentage: float
    quarter_hour_prices: tuple[QuarterHourlyPrice, ...]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HourlyPrice:
        """Parse the OpenAPI `HourlyPrice` shape without assuming units or VAT."""
        quarters = data.get("quarterHourPrices") or []
        if not isinstance(quarters, list):
            raise ProtocolError("quarterHourPrices must be a list or null")
        message = data.get("priceMessage")
        return cls(
            index=_int(data, "hourIndex"),
            valid_from=_datetime(data.get("validFrom"), "validFrom"),
            valid_to=_datetime(data.get("validTo"), "validTo"),
            distribution_price=_float(data, "distributionPrice"),
            tax=_float(data, "tax"),
            electricity_price=_float(data, "electricityPrice"),
            total_price=_float(data, "totalPrice"),
            price_state=_int(data, "priceState"),
            price_level=_int(data, "priceLevel"),
            price_message=message if isinstance(message, str) else None,
            distribution_price_percentage=_float(data, "distributionPricePercentage"),
            tax_percentage=_float(data, "taxPercentage"),
            electricity_price_percentage=_float(data, "electricityPricePercentage"),
            quarter_hour_prices=tuple(QuarterHourlyPrice.from_dict(item) for item in quarters),
        )

    @property
    def local_valid_from(self) -> datetime:
        """Return the timestamp in Europe/Copenhagen, preserving DST semantics."""
        return self.valid_from.astimezone(COPENHAGEN)


@dataclass(frozen=True, slots=True)
class PriceSeries:
    """Known or forecast price series and summary values."""

    prices: tuple[HourlyPrice, ...]
    average_price: float
    min_price: float
    max_price: float
    product_name: str | None = None
    issue_date: datetime | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, forecast: bool = False) -> PriceSeries:
        """Parse known prices or forecast prices."""
        prices = data.get("prices") or []
        if not isinstance(prices, list):
            raise ProtocolError("prices must be a list or null")
        issue = data.get("issueDate")
        product = data.get("productName")
        return cls(
            prices=tuple(HourlyPrice.from_dict(item) for item in prices),
            average_price=_float(data, "averagePrice"),
            min_price=_float(data, "minPrice"),
            max_price=_float(data, "maxPrice"),
            product_name=product if isinstance(product, str) else None,
            issue_date=_datetime(issue, "issueDate") if forecast and issue is not None else None,
        )


@dataclass(frozen=True, slots=True)
class FuturePricesForecast:
    """Combined v3 known-price and forecast response."""

    future_prices: PriceSeries
    forecast_prices: PriceSeries
    moving_average_hours: tuple[dict[str, Any], ...]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FuturePricesForecast:
        """Parse `GetFuturePricesForecastResponse`."""
        future = data.get("futurePrices")
        forecast = data.get("forecastPrices")
        moving = data.get("movingAverageHours") or []
        if not isinstance(future, dict) or not isinstance(forecast, dict):
            raise ProtocolError("futurePrices and forecastPrices must be objects")
        if not isinstance(moving, list) or not all(isinstance(item, dict) for item in moving):
            raise ProtocolError("movingAverageHours must be a list of objects")
        return cls(
            future_prices=PriceSeries.from_dict(future),
            forecast_prices=PriceSeries.from_dict(forecast, forecast=True),
            moving_average_hours=tuple(moving),
        )


@dataclass(frozen=True, slots=True)
class PriceProduct:
    """A product accepted by the future-prices endpoint."""

    product_id: int
    name: str | None
    text: str | None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PriceProduct:
        """Parse a future-price product."""
        name = data.get("productName")
        text = data.get("productText")
        return cls(
            product_id=_int(data, "futurePriceProductId"),
            name=name if isinstance(name, str) else None,
            text=text if isinstance(text, str) else None,
        )


@dataclass(frozen=True, slots=True)
class Address:
    """Address fields relevant to future-price selection."""

    street: str | None
    house_number: str | None
    postal_code: int
    postal_office: str | None
    floor: str | None
    door: str | None
    net_area: str | None

    @property
    def street_address(self) -> str:
        """Return street name and house number as shown by the app."""
        return " ".join(value for value in (self.street, self.house_number) if value)

    @property
    def formatted(self) -> str:
        """Return a compact human-readable Danish address."""
        street_line = self.street_address
        unit = " ".join(value for value in (self.floor, self.door) if value)
        if unit:
            street_line = f"{street_line}, {unit}" if street_line else unit
        postal_line = " ".join(
            value
            for value in (str(self.postal_code) if self.postal_code else None, self.postal_office)
            if value
        )
        return ", ".join(value for value in (street_line, postal_line) if value)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Address:
        """Parse a metering-point address."""
        def string(field: str) -> str | None:
            value = data.get(field)
            if not isinstance(value, str):
                return None
            normalized = " ".join(value.split())
            return normalized or None

        return cls(
            street=string("addressStreetName"),
            house_number=string("addressHouseNumber"),
            postal_code=_int(data, "addressPostalCode"),
            postal_office=string("addressPostalOfficeName"),
            floor=string("addressFloor"),
            door=string("addressDoor"),
            net_area=string("netArea"),
        )


@dataclass(frozen=True, slots=True)
class ElectricityProduct:
    """Product associated with a metering point."""

    product_id: int | None
    name: str | None
    text: str | None
    is_variable: bool

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ElectricityProduct:
        """Parse an electricity product."""
        product_id = data.get("electricityProductId")
        return cls(
            product_id=product_id if isinstance(product_id, int) else None,
            name=data.get("electricityProductName")
            if isinstance(data.get("electricityProductName"), str)
            else None,
            text=data.get("electricityProductText")
            if isinstance(data.get("electricityProductText"), str)
            else None,
            is_variable=data.get("isVariableProduct") is True,
        )


@dataclass(frozen=True, slots=True)
class MeteringPoint:
    """A user's metering point, address, product, and agreement state."""

    identification: str | None
    address: Address
    product: ElectricityProduct
    agreement_active: bool

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MeteringPoint:
        """Parse a v2 metering point."""
        address = data.get("address")
        product = data.get("electricityProduct")
        agreement = data.get("agreementDuration")
        if not isinstance(address, dict) or not isinstance(product, dict):
            raise ProtocolError("metering point address/product must be objects")
        active = agreement.get("isActive") is True if isinstance(agreement, dict) else False
        identification = data.get("meteringPointIdentification")
        return cls(
            identification=identification if isinstance(identification, str) else None,
            address=Address.from_dict(address),
            product=ElectricityProduct.from_dict(product),
            agreement_active=active,
        )
