"""Constants for the Ambient Weather Local integration."""

DOMAIN = "ambient_local"

# Config / options keys
CONF_CONSOLE_IP = "console_ip"
CONF_LISTEN_PORT = "listen_port"
CONF_DEVICE_NAME = "device_name"
CONF_SCAN_MINUTES = "scan_minutes"

# Defaults
DEFAULT_LISTEN_PORT = 8099
DEFAULT_DEVICE_NAME = "Home"
DEFAULT_SCAN_MINUTES = 5
DEFAULT_UPLOAD_SECONDS = 60

# The URL path the console must POST to. Without the "?" the console emits a
# malformed request line ("/&field=..."), so it is mandatory.
CONSOLE_PATH = "/?"

# Entities go unavailable after this many missed upload intervals (min 5 min).
STALE_INTERVAL_FACTOR = 4
STALE_MIN_SECONDS = 300
