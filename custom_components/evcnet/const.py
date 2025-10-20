"""Constants for the EVC-net integration."""

DOMAIN = "evcnet"

# Configuration
CONF_BASE_URL = "base_url"
CONF_CARD_ID = "card_id"
CONF_CUSTOMER_ID = "customer_id"

# Default values
DEFAULT_BASE_URL = "https://50five-sbelux.evc-net.com"
DEFAULT_SCAN_INTERVAL = 30  # seconds
MIN_SCAN_INTERVAL = 10  # minimum seconds between updates
MAX_SCAN_INTERVAL = 300  # maximum seconds between updates

# API endpoints
LOGIN_ENDPOINT = "/Login/Login"
AJAX_ENDPOINT = "/api/ajax"

# Attribute names
ATTR_RECHARGE_SPOT_ID = "recharge_spot_id"
ATTR_CHANNEL = "channel"
ATTR_STATUS = "status"
ATTR_POWER = "power"
ATTR_ENERGY = "energy"
ATTR_CUSTOMER_ID = "customer_id"
ATTR_CARD_ID = "card_id"

# Charging status codes
CHARGING_STATUS_CODES = {
    "CHARGING_NO_POWER": "10000",
    "CHARGING_PARTIAL": "20000", 
    "CHARGING_NORMAL": "30000",
    "CHARGING_HIGH": "40000",
    "CHARGING_FULL": "50000",
}

# Status code descriptions for user-friendly display
STATUS_DESCRIPTIONS = {
    "10000": "Charging (No Power)",
    "20000": "Charging (Partial)",
    "30000": "Charging (Normal)",
    "40000": "Charging (High)",
    "50000": "Charging (Complete)",
}

# Charging keywords for status detection
CHARGING_KEYWORDS = [
    "laden", "charging", "bezig", "actief", "vol", "full", "charge", "lading"
]
