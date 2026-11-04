# okhome

Unofficial Home Assistant integration and asynchronous Python client for the OK Hjem electricity
APIs, based on OK Hjem Android 2.10.0 and the published OpenAPI documents.

This project is independent of OK. Use it only with your own account and in accordance with the
service terms. It does not bypass certificate validation, App Check, or access control.

## Home Assistant installation through HACS

1. Open HACS and select **Integrations**.
2. Add `https://github.com/triangledog/okhome` as a custom repository of type **Integration**.
3. Install **OK Hjem** and restart Home Assistant.
4. Open **Settings > Devices & services > Add integration** and select **OK Hjem**.
5. Sign in and select the electricity agreement to expose.

The integration is fully self-contained under `custom_components/ok_home`; it does not download
the standalone `okhome` package from PyPI. The password is used only during setup or
reauthentication and is not persisted.

**If you install through HACS, stop here.** The Python installation and command-line examples below
are optional and only intended for development or standalone use.

## Status

- Implemented: device registration, READ/WRITE request-token renewal, email/password login,
  metering points, products, net-area lookup, v3 known prices, forecasts, and quarter-hour data.
- Live-verified: product lookup is anonymous; v3 prices reject missing request tokens with HTTP 401.
- Live-verified: device registration and READ-token issuance work without Firebase App Check.
- Live-verified: a pre-login installation token can fetch generic v3 known and forecast prices.
- Unit tested with recorded schema-shaped mock responses.
- Live-verified with email/password login and account-specific quarter-hour prices.
- `totalPrice` is verified as DKK/kWh and comprises distribution, tax, and electricity price.
- VAT inclusion is likely for the private-customer UI, but remains unverified.
- Live-verified: login and price retrieval work without a Firebase App Check header.

See [`docs/reverse-engineering.md`](docs/reverse-engineering.md) for endpoint details, data-model
limitations, and APK class/method references.

## Standalone Python client (optional)

This section is not required for Home Assistant or HACS.

### Development installation

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
```

Python 3.11 or newer is required.

### Standalone authentication configuration

The APK uses a per-installation UUID plus a component bundled in the official application to sign
bootstrap requests. The verified app protocol profile is included in the client so HACS users are
not asked to reverse engineer or configure it. `OKHOME_HMAC_KEY_PREFIX` remains available as an
optional override for standalone use. Normally, only provide your email and run the example:

```bash
export OKHOME_EMAIL='you@example.dk'
python examples/get_prices.py
```

The password is requested through a hidden terminal prompt. `OKHOME_PASSWORD` is supported for
automation but is intentionally not recommended for interactive use.

The example persists installation ID, server device ID, and opaque request tokens in
`~/.config/okhome/auth.json` with mode `0600`. Passwords and the profile `loginToken` are not
persisted or logged. The Home Assistant integration uses its own in-memory token storage and does
not use this file.

If a legitimate device ID and request token are already available from local secure storage,
`StaticTokenAuth` can call electricity endpoints without implementing bootstrap:

```python
from okhome import OKHomeClient, StaticTokenAuth

auth = StaticTokenAuth(device_identification="...", request_token="...")
async with OKHomeClient(auth) as client:
    products = await client.get_products()
```

The product list also works as `OKHomeClient()` without auth. Account, metering-point, net-area,
and price methods still require an auth provider.

Do not put these values in source code.

### Library example

```python
from pathlib import Path

from okhome import AuthConfig, FileAuthStore, OKHomeClient
from okhome.auth import OKHomeAuth

store = FileAuthStore(Path.home() / ".config/okhome/auth.json")
async with OKHomeAuth(AuthConfig.from_env(), store) as auth:
    await auth.login(email, password)
    async with OKHomeClient(auth) as client:
        points = await client.get_metering_points()
        prices = await client.get_prices_for_first_active_metering_point()
```

`get_prices_for_first_active_metering_point()` follows the same verified mapping as the Android
app: active metering point net area, electricity product ID, and postcode become the v3 price query.
For multiple addresses, select a returned `MeteringPoint` and call `get_future_prices()` explicitly.

## Data interpretation

Use `HourlyPrice.total_price` for the API's aggregate. Comparison with the OK Hjem UI verified that
`totalPrice` is DKK/kWh: the API quarter at 00:00-00:15 matched the app's displayed `2.37 kr/kWh`.
The API also returns `distribution_price`, `tax`, and `electricity_price`. A live response verified
that these three components reproduce `totalPrice` subject to display rounding: components have
more precision, while `totalPrice` has two decimals. The largest observed difference across 28
known hourly records was `0.004790 DKK/kWh`.

The OK Hjem UI defines the components as:

- `distributionPrice`: local grid tariffs and Energinet system tariffs for transporting power.
- `tax`: the state electricity duty charged per consumed kWh.
- `electricityPrice`: the hourly electricity price including the product-specific markup.

Neither the API response nor the app UI exposes spot price and OK markup as separate numeric
values. `electricityPrice` must therefore be treated as one combined value including markup. VAT
treatment also remains implicit rather than explicitly stated by OpenAPI or the response.

Every parsed timestamp must carry a UTC offset. `HourlyPrice.local_valid_from` converts it with the
IANA `Europe/Copenhagen` timezone, including DST. Quarter-hour entries are exposed exactly as
returned instead of assuming four fixed intervals.

Live responses for the tested OK agreement contained four `quarterHourPrices` in every known hour.
Forecast hours contained no quarter-hour entries, so the example prints known prices per quarter
and forecasts per hour. It never derives synthetic quarter-hour forecasts.

## Development checks

```bash
pytest
mypy okhome
ruff check .
```

Live testing requires local secrets and is intentionally separate from the unit suite. No test or
error logger prints passwords or opaque tokens.

## Home Assistant custom integration

The first HACS-oriented implementation is under `custom_components/ok_home`. It follows the Nord
Pool pattern: API responses stay in the coordinator's memory, sensors expose small current values,
and the `ok_home.get_prices` and `ok_home.get_forecast` response actions return complete series.
The API is called once at startup and then daily at 13:05 Europe/Copenhagen, shortly after the next
day's prices are normally published. If tomorrow's known prices are missing, the integration makes
at most three 15-minute retries. Entity listeners move to the next known quarter locally every 15
minutes without API calls.

The integration persists installation ID, device ID, account/profile metadata, and the selected
metering point in Home Assistant's config entry. Passwords and short-lived request tokens are not
persisted. Diagnostics redact account, address, device, and metering-point identifiers.

Devices are named from the metering point's street name and house number, matching the app. The
electricity product is retained as the device model.

The HACS integration is self-contained: it vendors the client under
`custom_components/ok_home/api` and has no PyPI dependency. The top-level `okhome` package remains
the canonical standalone implementation; its vendored integration copy must be kept synchronized
when the client changes.
