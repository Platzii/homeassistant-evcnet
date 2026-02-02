"""The EVC-net integration."""
import asyncio
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv, entity_registry as er, service
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import EvcNetApiClient
from .const import CONF_BASE_URL, DOMAIN, CONF_MAX_CHANNELS, DEFAULT_MAX_CHANNELS
from .coordinator import EvcNetCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.SWITCH, Platform.BUTTON]

# This integration can only be set up from config entries, not from YAML
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up is called when Home Assistant is loading our component."""

    async def async_handle_refresh_status(call: ServiceCall) -> None:
        """Handle the refresh_status action call."""
        entity_ids = await service.async_extract_entity_ids(call)

        if not entity_ids:
            _LOGGER.error(
                "Action refresh_status requires entity_id. "
                "Received call data: %s",
                call.data
            )
            return

        # Process each entity
        for entity_id in entity_ids:
            # Only process button entities
            if not entity_id.startswith("button."):
                continue

            # Get the entity registry to find which config entry this entity belongs to
            entity_registry = er.async_get(hass)
            entity_entry = entity_registry.async_get(entity_id)

            if not entity_entry:
                _LOGGER.error("Entity %s not found", entity_id)
                continue

            # Find the coordinator for this entity's config entry
            config_entry_id = entity_entry.config_entry_id
            if not config_entry_id or config_entry_id not in hass.data.get(DOMAIN, {}):
                _LOGGER.error("Could not find coordinator for entity %s", entity_id)
                continue

            coordinator = hass.data[DOMAIN][config_entry_id]

            # Extract spot_id from unique_id (format: {spot_id}_refresh_status)
            unique_id = entity_entry.unique_id
            if not unique_id or not unique_id.endswith("_refresh_status"):
                _LOGGER.debug("Skipping entity %s (not a refresh status button): %s", entity_id, unique_id)
                continue

            try:
                _LOGGER.info("Manually refreshing status via service call for entity %s", entity_id)
                await coordinator.async_request_refresh()
            except Exception as err:
                _LOGGER.error("Failed to refresh status: %s", err, exc_info=True)

    async def async_handle_start_charging(call: ServiceCall) -> None:
        """Handle the start_charging action call."""
        # Use service helper to expand target (handles services.yaml target resolution)
        # This properly expands entity_id from target, device, area, etc.
        entity_ids = await service.async_extract_entity_ids(call)

        card_id = call.data.get("card_id")

        if not entity_ids:
            _LOGGER.error(
                "Action start_charging requires entity_id. "
                "Received call data: %s",
                call.data
            )
            return

        # Process each entity
        for entity_id in entity_ids:
            # Only process switch entities (ignore sensors when device/area is selected)
            if not entity_id.startswith("switch."):
                continue

            # Get the entity registry to find which config entry this entity belongs to
            entity_registry = er.async_get(hass)
            entity_entry = entity_registry.async_get(entity_id)

            if not entity_entry:
                _LOGGER.error("Entity %s not found", entity_id)
                continue

            # Find the coordinator for this entity's config entry
            config_entry_id = entity_entry.config_entry_id
            if not config_entry_id or config_entry_id not in hass.data.get(DOMAIN, {}):
                _LOGGER.error("Could not find coordinator for entity %s", entity_id)
                continue

            coordinator = hass.data[DOMAIN][config_entry_id]

            # Extract spot_id from unique_id (format: {spot_id}_charging)
            unique_id = entity_entry.unique_id
            if not unique_id or not unique_id.endswith("_charging"):
                _LOGGER.debug("Skipping entity %s (not a charging switch): %s", entity_id, unique_id)
                continue

            # Get the entity from stored references
            entities_dict = getattr(coordinator, "entities", {})
            switch_entity = entities_dict.get(unique_id)

            if switch_entity and hasattr(switch_entity, 'async_turn_on'):
                # Call the entity's method directly with card_id
                await switch_entity.async_turn_on(card_id=card_id)
            else:
                _LOGGER.error("Could not find switch entity %s (unique_id: %s)", entity_id, unique_id)

    async def async_handle_charging_action(call: ServiceCall, action_name: str) -> None:
        """Handle charging station actions (soft_reset, hard_reset, unlock_connector, block, unblock)."""
        entity_ids = await service.async_extract_entity_ids(call)

        if not entity_ids:
            _LOGGER.error(
                "Action %s requires entity_id. Received call data: %s",
                action_name,
                call.data
            )
            return

        # Process each entity
        for entity_id in entity_ids:
            # Only process switch entities
            if not entity_id.startswith("switch."):
                continue

            # Get the entity registry to find which config entry this entity belongs to
            entity_registry = er.async_get(hass)
            entity_entry = entity_registry.async_get(entity_id)

            if not entity_entry:
                _LOGGER.error("Entity %s not found", entity_id)
                continue

            # Find the coordinator for this entity's config entry
            config_entry_id = entity_entry.config_entry_id
            if not config_entry_id or config_entry_id not in hass.data.get(DOMAIN, {}):
                _LOGGER.error("Could not find coordinator for entity %s", entity_id)
                continue

            coordinator = hass.data[DOMAIN][config_entry_id]

            # Determine spot and channel from the stored entity reference
            unique_id = entity_entry.unique_id
            entities_dict = getattr(coordinator, "entities", {})
            switch_entity = entities_dict.get(unique_id)

            if not switch_entity:
                _LOGGER.error("Could not find switch entity %s (unique_id: %s)", entity_id, unique_id)
                continue

            spot_id = getattr(switch_entity, "_spot_id", None)
            if spot_id is None:
                _LOGGER.error("Switch entity missing spot_id: %s", unique_id)
                continue

            # Prefer the entity's channel override when present; fallback to spot info
            spot_data = coordinator.data.get(spot_id, {})
            spot_info = spot_data.get("info", {})
            channel_override = getattr(switch_entity, "_channel_override", None)
            channel = str(channel_override or spot_info.get("CHANNEL", "1"))

            try:
                _LOGGER.info("Performing %s on spot %s, channel %s", action_name, spot_id, channel)

                # Call the appropriate API method
                if action_name == "soft_reset":
                    await coordinator.client.soft_reset(spot_id, channel)
                elif action_name == "hard_reset":
                    await coordinator.client.hard_reset(spot_id, channel)
                elif action_name == "unlock_connector":
                    await coordinator.client.unlock_connector(spot_id, channel)
                elif action_name == "block":
                    await coordinator.client.block(spot_id, channel)
                elif action_name == "unblock":
                    await coordinator.client.unblock(spot_id, channel)

                # Wait for the action to take effect
                await asyncio.sleep(3)

                # Refresh coordinator data
                await coordinator.async_request_refresh()

            except Exception as err:
                _LOGGER.error("Failed to perform %s: %s", action_name, err, exc_info=True)
                # Force refresh even on error
                await coordinator.async_request_refresh()

    # Register the actions - Home Assistant will load schema from services.yaml
    hass.services.async_register(
        DOMAIN,
        "refresh_status",
        async_handle_refresh_status,
    )

    hass.services.async_register(
        DOMAIN,
        "start_charging",
        async_handle_start_charging,
    )

    hass.services.async_register(
        DOMAIN,
        "soft_reset",
        lambda call: async_handle_charging_action(call, "soft_reset"),
    )

    hass.services.async_register(
        DOMAIN,
        "hard_reset",
        lambda call: async_handle_charging_action(call, "hard_reset"),
    )

    hass.services.async_register(
        DOMAIN,
        "unlock_connector",
        lambda call: async_handle_charging_action(call, "unlock_connector"),
    )

    hass.services.async_register(
        DOMAIN,
        "block",
        lambda call: async_handle_charging_action(call, "block"),
    )

    hass.services.async_register(
        DOMAIN,
        "unblock",
        lambda call: async_handle_charging_action(call, "unblock"),
    )

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up EVC-net from a config entry."""
    # Validate required configuration
    required_keys = [CONF_BASE_URL, CONF_USERNAME, CONF_PASSWORD]
    missing_keys = [key for key in required_keys if key not in entry.data]

    if missing_keys:
        _LOGGER.error(
            "Missing required configuration keys: %s. "
            "Please delete and re-add the integration: "
            "Settings > Devices & Services > EVC-net > Delete",
            missing_keys
        )
        return False

    session = async_get_clientsession(hass)

    client = EvcNetApiClient(
        entry.data[CONF_BASE_URL],
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
        session,
    )

    # Read max channels from options; default to DEFAULT_MAX_CHANNELS
    max_channels = int(entry.options.get(CONF_MAX_CHANNELS, DEFAULT_MAX_CHANNELS))
    coordinator = EvcNetCoordinator(hass, client, max_channels=max_channels)

    # Fetch initial data
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Listen for options updates
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    return True


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the config entry when it's updated."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
