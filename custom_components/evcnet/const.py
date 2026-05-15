"""Constants for the EVC-net integration."""

DOMAIN = "evcnet"

# Configuration
CONF_BASE_URL = "base_url"

# Default values
DEFAULT_BASE_URL = "https://50five-snl.evc-net.com"
DEFAULT_SCAN_INTERVAL = 60  # seconds
LOG_ROW_LIMIT = 50  # Max number of log entries to process/display

# API endpoints
LOGIN_ENDPOINT = "/Login/Login"
AJAX_ENDPOINT = "/api/ajax"

# GraphQL API (Plugz/LMS platform – used by the 50five mobile app)
GRAPHQL_ENDPOINT = "https://lms.servicelayer.test.platform-test.plugz.dev/graphql"
# 50five Plugz application ID embedded in the mobile-app JWT payload
PLUGZ_APPLICATION_ID = "dd6ee167-17d0-4bea-877d-9156bd5917dd"

# Key names
KEY_ID = "id"
KEY_TEXT = "text"
KEY_CUSTOMERS_IDX = "CUSTOMERS_IDX"
KEY_CUSTOMER_NAME = "CUSTOMER_NAME"
KEY_CARDS_IDX = "CARDS_IDX"
KEY_CARDID = "CARDID"

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
    "NO_COMMUNICATION": 0x30000000,  # No communication with charging station
    "FAULT": 0x4000002F,  # Various fault conditions
}

# Status2 flags (lower 32 bits)
CHARGESPOT_STATUS2_FLAGS = {
    "BLOCKED": 0x20000,  # Charging spot is blocked
    "OCCUPIED": 0x10000,  # Charging spot is occupied
    "FULL": 0x40000,  # Charging is complete/full
    "RESERVED": 0x400,  # Charging spot is reserved
    "FAULT": 0xD8407940,  # Various fault conditions
}
# Note: AVAILABLE state is represented by the absence of all status2 flags

PREPARE_STATUS_LIST = [
    "preparing",
    "transactie voorbereiden",
    "vorbereitung",
    "préparation",
]


class EvcNetException(Exception):
    """Base exception for EVC-net."""
