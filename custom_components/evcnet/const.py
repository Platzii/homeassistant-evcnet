"""Constants for the EVC-net integration."""

DOMAIN = "evcnet"

# Configuration
CONF_BASE_URL = "base_url"
CONF_CARD_ID = "card_id"
CONF_CUSTOMER_ID = "customer_id"
CONF_MAX_CHANNELS = "max_channels"

# Default values
DEFAULT_BASE_URL = "https://50five-sbelux.evc-net.com"
DEFAULT_SCAN_INTERVAL = 30  # seconds
DEFAULT_MAX_CHANNELS = 1

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

# Status flags for bitwise operations
# Status1 flags (upper 32 bits)
CHARGESPOT_STATUS1_FLAGS = {
    "NO_COMMUNICATION": 0x30000000, # No communication with charging station
    "FAULT": 0x4000002F,            # Various fault conditions
}

# Status2 flags (lower 32 bits)
CHARGESPOT_STATUS2_FLAGS = {
    "BLOCKED": 0x20000,             # Charging spot is blocked
    "OCCUPIED": 0x10000,            # Charging spot is occupied
    "FULL": 0x40000,                # Charging is complete/full
    "RESERVED": 0x400,              # Charging spot is reserved
    "FAULT": 0xD8407940,            # Various fault conditions
}
# Note: AVAILABLE state is represented by the absence of all status2 flags
