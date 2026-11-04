"""Constants for the OK Hjem integration."""

from datetime import timedelta

DOMAIN = "ok_home"
PLATFORMS = ["sensor"]

CONF_INSTALLATION_ID = "installation_id"
CONF_DEVICE_IDENTIFICATION = "device_identification"
CONF_PROFILE_NUMBER = "profile_number"
CONF_METERING_POINT = "metering_point"
CONF_NET_AREA = "net_area"
CONF_PRODUCT_ID = "product_id"
CONF_PRODUCT_NAME = "product_name"
CONF_POSTAL_CODE = "postal_code"

ATTR_CONFIG_ENTRY_ID = "config_entry"
SERVICE_GET_PRICES = "get_prices"
SERVICE_GET_FORECAST = "get_forecast"

API_UPDATE_INTERVAL = timedelta(hours=1)
PRICE_UNIT = "DKK/kWh"
