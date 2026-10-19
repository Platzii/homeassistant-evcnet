"""Constants for the EVC-net integration."""

DOMAIN = "evcnet"

# Configuration
CONF_CARD_ID = "card_id"
CONF_MAX_CHANNELS = "max_channels"
# Only used by config entries created before 2.0.0 (removed during migration)
CONF_BASE_URL = "base_url"
CONF_CUSTOMER_ID = "customer_id"

# Default values
DEFAULT_SCAN_INTERVAL = 30  # seconds
DEFAULT_MAX_CHANNELS = 1
ACTION_SETTLE_DELAY_SEC = 3 # Seconds to wait after start/stop/action before refreshing coordinator data

# Number of recent transactions fetched on every poll (used for the log sensors)
RECENT_TRANSACTIONS = 25
# Interval at which the full transaction history is re-read to recompute total energy
FULL_HISTORY_INTERVAL_SEC = 6 * 3600
# Retry delay when loading the transaction history failed
HISTORY_RETRY_SEC = 300

# Transaction status reported by the API while a session is running
TRANSACTION_STATUS_ACTIVE = "charging"
# Channel status reported while a transaction is ongoing (also when the car is full)
CHANNEL_STATUS_TRANSACTION = "Transaction"

# Attribute names
ATTR_RECHARGE_SPOT_ID = "recharge_spot_id"
ATTR_CHANNEL = "channel"
ATTR_CARD_ID = "card_id"
