"""Constants for TimeTree Enhanced integration."""

DOMAIN = "timetree_enhanced"

CONF_EMAIL = "email"
CONF_PASSWORD = "password"
CONF_CALENDAR_ID = "calendar_id"
CONF_CALENDAR_NAME = "calendar_name"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_TIMEZONE = "timezone"
CONF_FETCH_DAYS = "fetch_days"

DEFAULT_SCAN_INTERVAL = 60
DEFAULT_FETCH_DAYS = 60
DEFAULT_TIMEZONE = "Europe/Berlin"

DISPLAY_SEPARATOR = " · "

DEFAULT_LABEL_NAMES = {
    "label 1", "label 2", "label 3", "label 4",
    "label 5", "label 6", "label 7",
    "none", "kein", "keine", "–", "-",
}

NO_MEMBER = "Sonstige"

MEMBER_COLORS = [
    "red", "pink", "purple", "indigo", "blue",
    "cyan", "teal", "green", "yellow", "amber",
    "orange", "deep-orange", "brown",
]

HOLIDAY_LABEL_KEYWORDS = {
    "feiertag", "holiday", "public holiday", "gesetzlicher feiertag"
}
