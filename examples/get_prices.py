"""Log in and print known and forecast electricity prices."""

import asyncio
import getpass
import os
from pathlib import Path

from okhome.auth import AuthConfig, FileAuthStore, OKHomeAuth
from okhome.client import OKHomeClient
from okhome.models import PriceSeries


def print_prices(label: str, series: PriceSeries) -> None:
    """Print quarter-hour prices where supplied, otherwise hourly prices."""
    has_quarters = any(price.quarter_hour_prices for price in series.prices)
    interval = "quarter-hour" if has_quarters else "hourly"
    print(f"{label} ({interval}, DKK/kWh):")
    for price in series.prices:
        if price.quarter_hour_prices:
            for quarter in price.quarter_hour_prices:
                timestamp = quarter.local_valid_from.isoformat(timespec="minutes")
                print(f"  {timestamp}: {quarter.total_price:.4f}")
        else:
            timestamp = price.local_valid_from.isoformat(timespec="minutes")
            print(f"  {timestamp}: {price.total_price:.4f}")


async def main() -> None:
    """Run the example without printing credentials or tokens."""
    email = os.environ.get("OKHOME_EMAIL") or input("OK Hjem email: ")
    password = os.environ.get("OKHOME_PASSWORD") or getpass.getpass("OK Hjem password: ")
    store = FileAuthStore(Path.home() / ".config" / "okhome" / "auth.json")

    async with OKHomeAuth(AuthConfig.from_env(), store) as auth:
        await auth.login(email, password)
        async with OKHomeClient(auth) as client:
            result = await client.get_prices_for_first_active_metering_point()

    print(f"Product: {result.future_prices.product_name or 'unknown'}")
    print_prices("Known prices", result.future_prices)
    print_prices("Forecast prices", result.forecast_prices)


if __name__ == "__main__":
    asyncio.run(main())
