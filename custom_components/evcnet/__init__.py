"""The EVC-net integration."""
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import entity_registry as er, service
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import EvcNetApiClient
from .const import CONF_BASE_URL, DOMAIN
from .coordinator import EvcNetCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.SWITCH]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up is called when Home Assistant is loading our component."""

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

    # Register the action - Home Assistant will load schema from services.yaml
    hass.services.async_register(
        DOMAIN,
        "start_charging",
        async_handle_start_charging,
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

    coordinator = EvcNetCoordinator(hass, client)

    # Fetch initial data
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
