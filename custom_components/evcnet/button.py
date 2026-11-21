"""Button platform for EVC-net."""
import asyncio
import logging
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ACTION_SETTLE_DELAY_SEC, DOMAIN
from .coordinator import EvcNetCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up EVC-net buttons."""
    coordinator: EvcNetCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = []

    for spot_id in coordinator.data:
        # Add button entities for each charging spot
        entities.extend([
            EvcNetRefreshStatusButton(coordinator, spot_id),
            EvcNetSoftResetButton(coordinator, spot_id),
            EvcNetHardResetButton(coordinator, spot_id),
            EvcNetUnlockConnectorButton(coordinator, spot_id),
            EvcNetBlockButton(coordinator, spot_id),
            EvcNetUnblockButton(coordinator, spot_id),
        ])

    async_add_entities(entities)


class EvcNetButtonBase(CoordinatorEntity[EvcNetCoordinator], ButtonEntity):
    """Base class for EVC-net button entities."""

    def __init__(
        self,
        coordinator: EvcNetCoordinator,
        spot_id: str,
        button_type: str,
        name_suffix: str,
        icon: str,
    ) -> None:
        """Initialize the button."""
        super().__init__(coordinator)
        self._spot_id = spot_id
        self._button_type = button_type
        self._attr_unique_id = f"{spot_id}_{button_type}"
        self._attr_icon = icon

        # Get spot info from coordinator data
        spot_info = coordinator.data.get(spot_id, {}).get("info", {})

        # Use NAME field, or fallback to spot ID
        spot_name = spot_info.get("NAME")
        if not spot_name or spot_name.strip() == "":
            spot_name = f"Charge Spot {spot_id}"

        self._attr_name = f"{spot_name} {name_suffix}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, spot_id)},
            "name": spot_name,
            "manufacturer": "Last Mile Solutions",
            "model": "EVC-net Charging Station",
            "sw_version": spot_info.get("SOFTWARE_VERSION"),
        }

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return self._spot_id in self.coordinator.data

    async def _execute_action(self, action_method) -> None:
        """Execute the button action."""
        try:
            spot_data = self.coordinator.data.get(self._spot_id, {})
            spot_info = spot_data.get("info", {})
            channel = str(spot_info.get("CHANNEL", "1"))

            _LOGGER.info(
                "Executing %s on spot %s, channel %s",
                self._button_type,
                self._spot_id,
                channel
            )

            await action_method(self._spot_id, channel)

            # Wait for the action to take effect
            await asyncio.sleep(ACTION_SETTLE_DELAY_SEC)

            # Force a refresh to get the new state
            await self.coordinator.async_request_refresh()

        except Exception as err:
            _LOGGER.error("Failed to execute %s: %s", self._button_type, err, exc_info=True)
            # Force refresh even on error
            await self.coordinator.async_request_refresh()


class EvcNetRefreshStatusButton(EvcNetButtonBase):
    """Button to manually refresh status from the portal."""

    def __init__(self, coordinator: EvcNetCoordinator, spot_id: str) -> None:
        """Initialize the refresh status button."""
        super().__init__(
            coordinator,
            spot_id,
            "refresh_status",
            "Refresh Status",
            "mdi:refresh",
        )

    async def async_press(self) -> None:
        """Handle the button press."""
        # get_status(spot_id) has no channel param; wrap so _execute_action can call it
        await self._execute_action(
            lambda spot_id, channel: self.coordinator.client.get_status(spot_id)
        )


class EvcNetSoftResetButton(EvcNetButtonBase):
    """Button to perform a soft reset on the charging station."""

    def __init__(self, coordinator: EvcNetCoordinator, spot_id: str) -> None:
        """Initialize the soft reset button."""
        super().__init__(
            coordinator,
            spot_id,
            "soft_reset",
            "Soft Reset",
            "mdi:restart"
        )

    async def async_press(self) -> None:
        """Handle the button press."""
        await self._execute_action(self.coordinator.client.soft_reset)


class EvcNetHardResetButton(EvcNetButtonBase):
    """Button to perform a hard reset on the charging station."""

    def __init__(self, coordinator: EvcNetCoordinator, spot_id: str) -> None:
        """Initialize the hard reset button."""
        super().__init__(
            coordinator,
            spot_id,
            "hard_reset",
            "Hard Reset",
            "mdi:restart-alert"
        )

    async def async_press(self) -> None:
        """Handle the button press."""
        await self._execute_action(self.coordinator.client.hard_reset)


class EvcNetUnlockConnectorButton(EvcNetButtonBase):
    """Button to unlock the connector on the charging station."""

    def __init__(self, coordinator: EvcNetCoordinator, spot_id: str) -> None:
        """Initialize the unlock connector button."""
        super().__init__(
            coordinator,
            spot_id,
            "unlock_connector",
            "Unlock Connector",
            "mdi:lock-open-variant"
        )

    async def async_press(self) -> None:
        """Handle the button press."""
        await self._execute_action(self.coordinator.client.unlock_connector)


class EvcNetBlockButton(EvcNetButtonBase):
    """Button to block the charging station."""

    def __init__(self, coordinator: EvcNetCoordinator, spot_id: str) -> None:
        """Initialize the block button."""
        super().__init__(
            coordinator,
            spot_id,
            "block",
            "Block",
            "mdi:cancel"
        )

    async def async_press(self) -> None:
        """Handle the button press."""
        await self._execute_action(self.coordinator.client.block)


class EvcNetUnblockButton(EvcNetButtonBase):
    """Button to unblock the charging station."""

    def __init__(self, coordinator: EvcNetCoordinator, spot_id: str) -> None:
        """Initialize the unblock button."""
        super().__init__(
            coordinator,
            spot_id,
            "unblock",
            "Unblock",
            "mdi:check-circle"
        )

    async def async_press(self) -> None:
        """Handle the button press."""
        await self._execute_action(self.coordinator.client.unblock)
