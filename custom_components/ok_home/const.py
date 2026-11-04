"""Constants for the OK Hjem integration."""

DOMAIN = "ok_home"
PLATFORMS = ["sensor"]

CONF_INSTALLATION_ID = "installation_id"
CONF_DEVICE_IDENTIFICATION = "device_identification"
CONF_PROFILE_NUMBER = "profile_number"
CONF_METERING_POINT = "metering_point"
CONF_ADDRESS = "address"
CONF_NET_AREA = "net_area"
CONF_PRODUCT_ID = "product_id"
CONF_PRODUCT_NAME = "product_name"
CONF_POSTAL_CODE = "postal_code"

ATTR_CONFIG_ENTRY_ID = "config_entry"
SERVICE_GET_PRICES = "get_prices"
SERVICE_GET_FORECAST = "get_forecast"

PRICE_PUBLICATION_HOUR = 13
PRICE_PUBLICATION_MINUTE = 5
PRICE_RETRY_MINUTES = 15
PRICE_MAX_RETRIES = 3
PRICE_UNIT = "DKK/kWh"
