"""Constants for the GreenFlux integration."""

from datetime import timedelta

from homeassistant.const import Platform

DOMAIN = "greenflux"
NAME = "GreenFlux"
API_VERSION = "1.0"

CONF_API_URL = "api_url"
CONF_API_TOKEN = "api_token"
CONF_AUTO_CREATE = "auto_create"
CONF_CHARGER_ID = "charger_id"
CONF_PLATFORM_NUMBER = "platform_number"

SUBENTRY_TYPE_CHARGER = "charger"

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BUTTON]

# Limit bulk station data requests to once per hour; notifications are separate.
STATION_UPDATE_INTERVAL = timedelta(hours=1)
CHARGE_STATION_LIMIT = 2147483647
NOTIFICATION_UPDATE_INTERVAL = timedelta(minutes=1)
INITIAL_NOTIFICATION_LOOKBACK = timedelta(hours=71)
NOTIFICATION_OVERLAP = timedelta(minutes=2)
NOTIFICATION_LIMIT = 100000
METER_VALUE_LOOKBACK = timedelta(minutes=30)
METER_VALUE_LIMIT = 10000
METER_VALUE_INITIAL_LOOKBACK = timedelta(minutes=10)
METER_VALUE_OVERLAP = timedelta(minutes=2)
METER_VALUE_DELAY = timedelta(seconds=45)

DEFAULT_STATUS = "UNKNOWN"
STATUS_OPTIONS = [
    "AVAILABLE",
    "BLOCKED",
    "CHARGING",
    "INOPERATIVE",
    "OUTOFORDER",
    "PLANNED",
    "REMOVED",
    "RESERVED",
    "UNKNOWN",
]
