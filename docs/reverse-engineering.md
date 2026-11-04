# Reverse engineering notes for OK Hjem 2.10.0

This document separates verified observations, inferences, and unresolved questions. The APK
analyzed was `dk.ok.home`, version `2.10.0` (`versionCode=1144`), SHA-256
`3fa8a97859d230269e1c49c4841bdeef6f2acf2c5f71949e48b3022ce5be724d`.

## Verified architecture

`ok-home-app-service.okcloud.dk` owns installation registration, custom email/password login,
request-token issuance, user-address configuration, and device/account association.
`electricity-home.okcloud.dk` owns products, net-area lookup, metering points, consumption, known
prices, and forecasts.

OpenAPI sources:

- `https://ok-home-app-service.okcloud.dk/swagger/v1/swagger.json`
- `https://electricity-home.okcloud.dk/swagger/v1/swagger.json`

The app-service OpenAPI advertises an Auth0 OAuth2 authorization-code scheme, but the 2.10.0 APK's
email/password flow does not use Auth0/OIDC access or refresh tokens. It calls the custom
`POST /api/v1/Login` endpoint. The returned profile has an opaque `loginToken`; the app does not
treat it as an OAuth token.

## Verified authentication flow

1. The app creates a random installation UUID and stores it under `dk.ok.home.app-id`.
2. It calls `POST /api/v2/RegisterDevice` with `{"deviceSecret":"<installation UUID>"}`.
3. The server returns `device.deviceIdentification`, persisted under `dk.ok.home.device`.
4. The app obtains a bare Base64 request-token string from
   `GET /api/v1/GetRequestToken?writePermission=false|true`.
5. The decoded token contains `Version`, `Expires`, `HasWritePermission`, and `Data`.
6. The original Base64 string, not decoded `Data`, becomes `X-App-Request-Token`.
7. Email/password login obtains a write request token first, then posts `emailAddress` and
   `password` to `/api/v1/Login`.
8. Login/logout clears cached request tokens. Expired tokens are renewed by token type.

The request-token issuance request has no email, user ID, login token, or existing request token.
It is directly tied to the installation/device proof. After login, server-side device association
may make newly issued tokens user-aware, but their server-side claims are not visible in the APK.

### HMAC

The following is verified from `C0943s0.java`, `ar4.java`, `df9.java`, and the URL helper
`fz4.java`:

```text
key = bundled APK component || installation UUID
fields = endpoint path + epoch seconds [+ server device ID when registered]
canonical = compact JSON array of singleton objects sorted by field name
signature = lowercase hex HMAC-SHA256(key, canonical)
```

Example canonical payload after registration:

```json
[{"deviceIdentification":"..."},{"endpoint":"/api/v1/GetRequestToken"},{"timestamp":1234567890}]
```

The query string is not signed. Headers are `X-App-Hmac` and `X-App-Timestamp`. The APK-bundled
component is included in the versioned client protocol profile so a HACS installation can operate
without local manual configuration. It is application-wide, not a user's account credential, but
publishing it is an explicit compatibility and service-terms maintenance decision.

Firebase App Check is attempted for registration, request-token issuance, and login. The Android
interceptor is fail-open. Live tests verified that device registration and READ-token issuance are
accepted without Firebase App Check; login has not yet been tested.

### APK evidence

The decompiled references below are relative to JADX output `sources/`:

| Finding | Decompiled evidence |
|---|---|
| Installation UUID generation/storage | `p000/r40.java:109-136`, `p000/s40.java:30-35` |
| Registration call and body | `p000/r83.java:13-15`, `p000/l83.java:14-27` |
| Device ID persistence/header | `p000/q73.java:24-39`, `p000/u11.java:71-88` |
| Token endpoint and READ/WRITE selection | `p000/yw8.java:9-11`, `p000/w37.java:1007-1015` |
| Token decode/cache/expiry | `p000/xw8.java:83-91`, `p000/vw8.java:114-147` |
| Token header | `p000/tw8.java:17-26` |
| Email/password login | `p000/df0.java:36-39`, `p000/cf0.java:284-326` |
| Login token persistence in app | `p000/fe0.java:19-27`, `p000/s40.java:41-45` |
| HMAC fields/canonicalization | `p000/C0943s0.java:202-233` |
| Future-price v3 endpoint | `p000/ca8.java:9-11` |
| Metering-point parameter selection | `p000/kb9.java:34-46`, `p000/vj3.java:509-520` |
| Saved-address selection | `p000/vb9.java:97-133` |

No certificate pinning configured by the app was found. This project does not bypass TLS or other
access controls.

## Endpoint overview

All electricity endpoints require `caller`, `X-App-Device-Identification`, and
`X-App-Request-Token` according to OpenAPI.

| Method and path | Role |
|---|---|
| `POST /api/v2/RegisterDevice` (app-service) | Registers installation; returns device ID |
| `GET /api/v1/GetRequestToken` (app-service) | Issues READ/WRITE opaque request token |
| `POST /api/v1/Login` (app-service) | Custom email/password login |
| `POST /api/v2/GetFutureElectricityPricesAddressList` (app-service) | Saved manual addresses |
| `GET /api/v1/Prices/GetProductsForFuturePrices` | Future-price products |
| `GET /api/v1/Prices/GetNetAreaFromAdress` | Net area from address (`Adress` is API spelling) |
| `GET /api/v1/Prices/GetNetAreaFromDarReference` | Net area from DAR UUID |
| `GET /api/v2/UserProfile/GetMeteringPoints` | Account addresses, agreement, and product |
| `GET /api/v3/Prices/GetFuturePrices` | Known prices, forecasts, moving averages |
| `GET /api/v1/Consumption/GetConsumptionDataForMeteringPoint` | Consumption hierarchy |

### Anonymous live checks

Checked on 2026-11-04 without credentials, request tokens, or device headers:

- `GET /api/v1/Prices/GetProductsForFuturePrices` returned HTTP `200` and product IDs `8` through
  `12`. This endpoint is therefore publicly callable despite OpenAPI marking auth-like headers as
  required.
- `GET /api/v3/Prices/GetFuturePrices?NetArea=DK1&FuturePriceProductId=1&PostalCode=8000` returned
  HTTP `401`, JSON code `5003`, and message `Forkert request token format`.
- Device registration and READ request-token issuance succeeded without login or Firebase App
  Check when the APK's version metadata and HMAC protocol were supplied.
- With that pre-login installation token, net-area lookup for a public test address succeeded and
  v3 returned both known and forecast prices for generic product ID `8`. An active user session is
  therefore not required for generic price data.

No authenticated live requests were made.

The app maps an active metering point's `address.netArea`,
`electricityProduct.electricityProductId`, and `address.addressPostalCode` directly to v3's
`NetArea`, `FuturePriceProductId`, and `PostalCode`.

## Price model

`GetFuturePricesForecastResponse` contains `futurePrices`, `forecastPrices`, and
`movingAverageHours`. Both price series use `HourlyPrice`, whose documented numeric components are
`distributionPrice`, `tax`, `electricityPrice`, and `totalPrice`. v3 additionally supplies
`quarterHourPrices`, each with `validFrom`, `validTo`, and `totalPrice`.

The numeric `totalPrice` unit is not documented by OpenAPI, but it is **live-verified as DKK/kWh**:
the API's 00:00-00:15 quarter matched the OK Hjem UI value `2.37 kr/kWh` for the same agreement and
interval.

The aggregate consists of `distributionPrice`, `tax`, and `electricityPrice`. Across 28 live
known-price hours, their high-precision sum reproduced the two-decimal `totalPrice` subject to
rounding; the maximum observed difference was `0.004790 DKK/kWh`. Quarter-hour entries expose only
`totalPrice`, not the individual components.

The app's own information text provides these verified semantic definitions:

- `distributionPrice` covers local grid tariffs and Energinet system tariffs charged for
  transporting electricity to the customer. Grid tariffs vary by time of day independently of the
  selected electricity product.
- `tax` is the state electricity duty imposed per consumed kWh.
- `electricityPrice` is the hourly electricity price including markup and depends on the selected
  electricity product.

The app describes `electricityPrice` as including markup. Neither the app UI nor the API response
splits spot price and OK markup into separate numeric fields, so the client preserves it as one
combined component.

The following remain **not documented** by OpenAPI and unverified:

- Whether values include VAT. Inclusion is likely because the price is presented to a private
  customer, but neither OpenAPI nor the inspected response states this explicitly.
- Whether `tax` is only electricity duty or combines further charges.
- Whether `electricityPrice` combines spot price and OK markup.
- OpenAPI does not guarantee the component sum, though it was verified empirically for the sampled
  response, allowing for `totalPrice` rounding.
- Exact enum meanings for `priceState` and `priceLevel`.

The API supports quarter-hour records structurally. The contract does not guarantee exactly four
15-minute records per hour, so the client preserves timestamps rather than deriving intervals.
Timestamps must include an offset; the client then converts them with `Europe/Copenhagen`, which
handles summer/winter time and repeated local hours.

An authenticated live response on 2026-11-04 contained four quarter-hour entries in each of 28
known-price hours (112 entries total). Its 120 forecast hours had empty quarter-hour lists. Thus,
known prices are available per quarter for this agreement, while the returned forecast is hourly.

## Unresolved

- Server enforcement of Firebase App Check specifically on email/password login.
- Token `Data` claims and precise account binding after login.
- Explicit confirmation of VAT treatment. A numeric spot/OK-markup split is unavailable in both
  the app UI and observed API response.
- Which electricity endpoints other than the verified public product list permit anonymous calls.
- Auth0/MitID flow, which is not needed for the prioritized email/password path.
