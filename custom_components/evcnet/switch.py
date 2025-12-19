"""Switch platform for EVC-net."""
import asyncio
import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_CARD_ID, CONF_CUSTOMER_ID, DOMAIN, CHARGESPOT_STATUS1_FLAGS, CHARGESPOT_STATUS2_FLAGS
from .coordinator import EvcNetCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up EVC-net switches."""
    coordinator: EvcNetCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = []
    # Initialize entities dict on coordinator if not exists
    if not hasattr(coordinator, "entities"):
        coordinator.entities = {}

    for spot_id, spot_data in coordinator.data.items():
        channels = spot_data.get("channels") or ["1"]
        for channel in channels:
            switch_entity = EvcNetChargingSwitch(coordinator, spot_id, channel, entry)
            entities.append(switch_entity)

            # Store entity reference by unique_id for action access
            coordinator.entities[switch_entity._attr_unique_id] = switch_entity

    async_add_entities(entities)


class EvcNetChargingSwitch(CoordinatorEntity[EvcNetCoordinator], SwitchEntity):
    """Representation of a EVC-net charging switch."""

    _attr_icon = "mdi:ev-station"

    def __init__(
        self,
        coordinator: EvcNetCoordinator,
        spot_id: str,
        channel: str,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator)
        self._spot_id = spot_id
        self._channel = str(channel)
        self._entry = entry
        self._attr_unique_id = f"{spot_id}_ch{self._channel}_charging"

        # Get spot info from coordinator data
        spot_info = coordinator.data.get(spot_id, {}).get("info", {})

        # Use NAME field, or fallback to spot ID
        spot_name = spot_info.get("NAME")
        if not spot_name or spot_name.strip() == "":
            spot_name = f"Charge Spot {spot_id}"

        self._attr_name = f"{spot_name} Charging Ch {self._channel}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, spot_id)},
            "name": spot_name,
            "manufacturer": "Last Mile Solutions",
            "model": "EVC-net Charging Station",
            "sw_version": spot_info.get("SOFTWARE_VERSION"),
        }

        # Store customer and card IDs for starting transactions
        # Priority: 1) Options, 2) Config entry, 3) Auto-detected from API
        self._customer_id = entry.options.get(CONF_CUSTOMER_ID) or entry.data.get(CONF_CUSTOMER_ID)
        self._card_id = entry.options.get(CONF_CARD_ID) or entry.data.get(CONF_CARD_ID)

        # Try to extract card_id from current data if not manually configured
        if not self._card_id:
            self._extract_card_id_from_data()

        if self._card_id:
            source = "options" if entry.options.get(CONF_CARD_ID) else ("config" if entry.data.get(CONF_CARD_ID) else "auto-detected")
            _LOGGER.info(
                "Card ID configured for spot %s: %s (source: %s)",
                spot_id,
                self._card_id,
                source
            )

    def _extract_card_id_from_data(self) -> None:
        """Extract card_id from coordinator data."""
        spot_data = self.coordinator.data.get(self._spot_id, {})
        status = spot_data.get("status", [])

        if self._is_valid_status_data(status):
            status_info = self._get_status_info_for_channel(status) or {}

            # Only auto-detect if not already set from config
            if not self._card_id and "CARDID" in status_info and status_info["CARDID"]:
                self._card_id = status_info["CARDID"]
                _LOGGER.info("Auto-detected card_id: %s for spot %s", self._card_id, self._spot_id)

    def _is_valid_status_data(self, status: list) -> bool:
        """Validate status data structure."""
        return (isinstance(status, list) and
                len(status) > 0 and
                isinstance(status[0], list) and
                len(status[0]) > 0)

    def _parse_status_flags(self, status_value: int) -> tuple[int, int]:
        """Parse status value into two 32-bit integers."""
        hex_status = str(status_value).zfill(16)
        status1 = int(hex_status[0:8], 16)  # First 8 hex chars (upper 32 bits)
        status2 = int(hex_status[8:], 16)   # Last 8 hex chars (lower 32 bits)
        return status1, status2

    def _has_error_conditions(self, status1: int, status2: int) -> bool:
        """Check for error conditions that prevent charging."""
        # Check for no communication (highest priority)
        if status1 & CHARGESPOT_STATUS1_FLAGS["NO_COMMUNICATION"]:
            return True

        # Check if blocked
        if status2 & CHARGESPOT_STATUS2_FLAGS["BLOCKED"]:
            return True

        # Check for fault conditions
        if (status1 & CHARGESPOT_STATUS1_FLAGS["FAULT"] or
            status2 & CHARGESPOT_STATUS2_FLAGS["FAULT"]):
            return True

        return False

    def _is_charging_active(self, status2: int) -> bool:
        """Check if charging is currently active."""
        return bool(status2 & CHARGESPOT_STATUS2_FLAGS["OCCUPIED"])

    def _get_status_info_for_channel(self, status: list) -> dict[str, Any] | None:
        """Return status info dict for this entity's channel."""
        if (isinstance(status, list) and status and isinstance(status[0], list)):
            for item in status[0]:
                if isinstance(item, dict) and str(item.get("CHANNEL")) == self._channel:
                    return item
            # Fallback to first item
            first = status[0][0]
            return first if isinstance(first, dict) else None
        return None

    @property
    def is_on(self) -> bool:
        """Return true if charging is active."""
        # Try to extract card_id from current data if not in config
        if not self._card_id:
            self._extract_card_id_from_data()

        spot_data = self.coordinator.data.get(self._spot_id, {})
        status = spot_data.get("status", [])

        if not self._is_valid_status_data(status):
            return False

        status_info = self._get_status_info_for_channel(status) or {}
        status_value = status_info.get("STATUS")

        if status_value is None:
            return False

        status1, status2 = self._parse_status_flags(status_value)

        # Check for error conditions first
        if self._has_error_conditions(status1, status2):
            return False

        # Check operational status
        return self._is_charging_active(status2)

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        # Entity is available if we have data for this spot
        return self._spot_id in self.coordinator.data

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Start charging."""
        try:
            # Get card_id from kwargs (action call) or use configured/default card
            card_id = kwargs.get("card_id") or self._card_id

            # If we don't have card_id, we need to get it from the user
            if not card_id:
                _LOGGER.error(
                    "Cannot start charging: card_id not available for spot %s. "
                    "Please reconfigure the integration and provide your RFID card ID, "
                    "or start a charging session manually once to auto-detect it, "
                    "or call the action with a card_id parameter.",
                    self._spot_id
                )
                return

            # Use empty string for customer_id if None (the API seems to accept this)
            customer_id = str(self._customer_id) if self._customer_id else ""

            _LOGGER.info(
                "Starting charging for spot %s on channel %s with card %s (customer: %s)",
                self._spot_id,
                self._channel,
                card_id,
                customer_id or "none"
            )

            await self.coordinator.client.start_charging(
                self._spot_id,
                customer_id,
                card_id,
                self._channel
            )

            # Wait a bit for the charging station to process the command
            # before refreshing the status
            await asyncio.sleep(3)

            # Force a refresh to get the new state
            await self.coordinator.async_request_refresh()

            # Also update this entity to reflect the change
            self.async_write_ha_state()

        except Exception as err:
            _LOGGER.error("Failed to start charging: %s", err, exc_info=True)

            # Force refresh even on error to get accurate state
            await self.coordinator.async_request_refresh()

            # Also update this entity to reflect the change
            self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Stop charging."""
        try:
            _LOGGER.info("Stopping charging for spot %s on channel %s", self._spot_id, self._channel)

            await self.coordinator.client.stop_charging(self._spot_id, self._channel)

            # Wait a bit for the charging station to process the command
            # before refreshing the status
            await asyncio.sleep(3)

            # Force a refresh to get the new state
            await self.coordinator.async_request_refresh()

            # Also update this entity to reflect the change
            self.async_write_ha_state()

        except Exception as err:
            _LOGGER.error("Failed to stop charging: %s", err)

            # Force refresh even on error to get accurate state
            await self.coordinator.async_request_refresh()

            # Also update this entity to reflect the change
            self.async_write_ha_state()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        spot_data = self.coordinator.data.get(self._spot_id, {})

        # Try to get more details from status
        status = spot_data.get("status", [])
        status_info: dict[str, Any] = {}
        if self._is_valid_status_data(status):
            status_info = self._get_status_info_for_channel(status) or {}

        attributes = {
            "spot_id": self._spot_id,
            "status": status_info.get("STATUS"),
            "power_kw": status_info.get("MOM_POWER_KW"),
            "transaction_energy_kwh": status_info.get("TRANS_ENERGY_DELIVERED_KWH"),
            "transaction_time": status_info.get("TRANSACTION_TIME_H_M"),
            "customer_id": self._customer_id,
            "card_id": self._card_id,
            "channel": self._channel,
        }

        # Remove None values
        return {k: v for k, v in attributes.items() if v is not None}
