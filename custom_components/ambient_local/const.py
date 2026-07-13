"""Constants for the Ambient Weather Local integration."""

DOMAIN = "ambient_local"

# Config / options keys
CONF_CONSOLE_IP = "console_ip"
CONF_LISTEN_PORT = "listen_port"
CONF_DEVICE_NAME = "device_name"
CONF_SCAN_MINUTES = "scan_minutes"

# Defaults
# 7080 matches the port the console/old add-on already used, so no reconfigure
# is needed on cutover. (Avoid 8099 — the SSH add-on's ttyd binds it.)
DEFAULT_LISTEN_PORT = 7080
DEFAULT_DEVICE_NAME = "Home"
DEFAULT_SCAN_MINUTES = 5
DEFAULT_UPLOAD_SECONDS = 60

# The URL path the console must POST to. Without the "?" the console emits a
# malformed request line ("/&field=..."), so it is mandatory.
CONSOLE_PATH = "/?"

# Entities go unavailable after this many missed upload intervals (min 5 min).
STALE_INTERVAL_FACTOR = 4
STALE_MIN_SECONDS = 300

# --- Provisioning (AP-mode recovery) -----------------------------------------
# In setup/AP mode the console broadcasts an OPEN SSID "AMBWeatherPro-<MACsuffix>"
# and serves its web UI at 192.168.4.1.
AP_SSID_PREFIX = "AMBWeatherPro-"
AP_HOST = "192.168.4.1"
# Persisted snapshot of the console's config so recovery can restore everything.
STORE_KEY = DOMAIN + "_console_cache"
STORE_VERSION = 1
