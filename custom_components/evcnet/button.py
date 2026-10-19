"""Button platform for EVC-net."""
import asyncio
from collections.abc import Awaitable, Callable
import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import ACTION_SETTLE_DELAY_SEC, DOMAIN
from .coordinator import EvcNetCoordinator
from .entity import EvcNetEntity

_LOGGER = logging.getLogger(__name__)

# (button type, name, icon)
BUTTON_TYPES: tuple[tuple[str, str, str], ...] = (
    ("refresh_status", "Refresh Status", "mdi:refresh"),
    ("soft_reset", "Soft Reset", "mdi:restart"),
    ("hard_reset", "Hard Reset", "mdi:restart-alert"),
    ("unlock_connector", "Unlock Connector", "mdi:lock-open-variant"),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up EVC-net buttons."""
    coordinator: EvcNetCoordinator = hass.data[DOMAIN][entry.entry_id]

    async_add_entities(
        EvcNetButton(coordinator, spot_id, button_type, name, icon)
        for spot_id in coordinator.data
        for button_type, name, icon in BUTTON_TYPES
    )


async def async_run_action(coordinator: EvcNetCoordinator, action_name: str, spot_id: str, channel_id: str | None) -> None:
    """Run a station action by name and refresh the coordinator afterwards."""
    client = coordinator.client
    actions: dict[str, Callable[[], Awaitable[bool]]] = {
        "soft_reset": lambda: client.soft_reset(spot_id),
        "hard_reset": lambda: client.hard_reset(spot_id),
        "unlock_connector": lambda: client.unlock_connector(spot_id, channel_id or ""),
    }
    try:
        if action_name in actions:
            if action_name == "unlock_connector" and not channel_id:
                raise ValueError(f"No channel found for spot {spot_id}")
            _LOGGER.info("Executing %s on spot %s", action_name, spot_id)
            await actions[action_name]()
            # Wait for the action to take effect
            await asyncio.sleep(ACTION_SETTLE_DELAY_SEC)
    except Exception as err:  # pylint: disable=broad-except
        _LOGGER.error("Failed to execute %s: %s", action_name, err)
    await coordinator.async_request_refresh()


class EvcNetButton(EvcNetEntity, ButtonEntity):
    """Button that performs an action on a charging station."""

    def __init__(
        self,
        coordinator: EvcNetCoordinator,
        spot_id: str,
        button_type: str,
        name_suffix: str,
        icon: str,
    ) -> None:
        """Initialize the button."""
        super().__init__(coordinator, spot_id)
        self._button_type = button_type
        self._attr_unique_id = f"{spot_id}_{button_type}"
        self._attr_name = f"{self._spot_name} {name_suffix}"
        self._attr_icon = icon

    async def async_press(self) -> None:
        """Handle the button press."""
        await async_run_action(self.coordinator, self._button_type, self._spot_id, self.channel_id)
