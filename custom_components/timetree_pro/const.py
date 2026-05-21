"""Constants for TimeTree Pro integration."""

DOMAIN = "timetree_pro"

# Config / Options keys
CONF_EMAIL = "email"
CONF_PASSWORD = "password"
CONF_CALENDAR_ID = "calendar_id"
CONF_CALENDAR_NAME = "calendar_name"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_TIMEZONE = "timezone"
CONF_FETCH_DAYS = "fetch_days"

# Defaults
DEFAULT_SCAN_INTERVAL = 60   # minutes
DEFAULT_FETCH_DAYS = 60      # days of upcoming events to fetch
DEFAULT_TIMEZONE = "Europe/Berlin"

# Display
DISPLAY_SEPARATOR = " · "    # e.g.  "Mama · Zahnarzt"

# Label names that are TimeTree defaults → not used as member names
DEFAULT_LABEL_NAMES = {
    "label 1", "label 2", "label 3", "label 4",
    "label 5", "label 6", "label 7",
    "none", "kein", "keine", "–", "-",
}

# Internal sentinel for events without a detectable member
NO_MEMBER = "Sonstige"

# HA entity color names, cycled per member
MEMBER_COLORS = [
    "red", "pink", "purple", "indigo", "blue",
    "cyan", "teal", "green", "yellow", "amber",
    "orange", "deep-orange", "brown",
]
