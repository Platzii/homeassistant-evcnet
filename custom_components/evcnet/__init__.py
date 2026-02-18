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
from .const import (
    ACTION_SETTLE_DELAY_SEC,
    CONF_BASE_URL,
    CONF_MAX_CHANNELS,
    DEFAULT_MAX_CHANNELS,
    DOMAIN,
)
from .coordinator import EvcNetCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.SWITCH, Platform.BUTTON]

# This integration can only be set up from config entries, not from YAML
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


def _resolve_evcnet_entity(
    hass: HomeAssistant,
    entity_id: str,
    expected_domain: str,
    unique_id_suffix: str | None = None,
) -> tuple[EvcNetCoordinator, er.RegistryEntry, Any] | None:
    """Resolve entity_id to coordinator, entity registry entry, and entity instance (for switches).

    Returns (coordinator, entity_entry, resolved_entity) or None if the entity is not
    valid for this integration. resolved_entity is None for button domain; for switch
    domain it is the entity from coordinator.entities when available.
    """
    if not entity_id.startswith(f"{expected_domain}."):
        return None
    entity_registry = er.async_get(hass)
    entity_entry = entity_registry.async_get(entity_id)
    if not entity_entry:
        _LOGGER.error("Entity %s not found", entity_id)
        return None
    config_entry_id = entity_entry.config_entry_id
    if not config_entry_id or config_entry_id not in hass.data.get(DOMAIN, {}):
        _LOGGER.error("Could not find coordinator for entity %s", entity_id)
        return None
    coordinator = hass.data[DOMAIN][config_entry_id]
    unique_id = entity_entry.unique_id
    if unique_id_suffix and (not unique_id or not unique_id.endswith(unique_id_suffix)):
        _LOGGER.debug("Skipping entity %s (wrong type): %s", entity_id, unique_id)
        return None
    resolved_entity = None
    if expected_domain == "switch":
        resolved_entity = getattr(coordinator, "entities", {}).get(unique_id)
    return (coordinator, entity_entry, resolved_entity)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up is called when Home Assistant is loading our component."""

    async def async_handle_start_charging(call: ServiceCall) -> None:
        """Handle the start_charging action call."""
        entity_ids = await service.async_extract_entity_ids(call)
        if not entity_ids:
            _LOGGER.error(
                "Action start_charging requires entity_id. Received call data: %s",
                call.data,
            )
            return
        card_id = call.data.get("card_id")
        for entity_id in entity_ids:
            resolved = _resolve_evcnet_entity(
                hass, entity_id, "switch", unique_id_suffix="_charging"
            )
            if not resolved:
                continue
            _coordinator, _entry, switch_entity = resolved
            if switch_entity and hasattr(switch_entity, "async_turn_on"):
                await switch_entity.async_turn_on(card_id=card_id)
            else:
                _LOGGER.error(
                    "Could not find switch entity %s (unique_id: %s)",
                    entity_id,
                    _entry.unique_id,
                )

    async def async_handle_stop_charging(call: ServiceCall) -> None:
        """Handle the stop_charging action call."""
        entity_ids = await service.async_extract_entity_ids(call)
        if not entity_ids:
            _LOGGER.error(
                "Action stop_charging requires entity_id. Received call data: %s",
                call.data,
            )
            return
        for entity_id in entity_ids:
            resolved = _resolve_evcnet_entity(
                hass, entity_id, "switch", unique_id_suffix="_charging"
            )
            if not resolved:
                continue
            _coordinator, _entry, switch_entity = resolved
            if switch_entity and hasattr(switch_entity, "async_turn_off"):
                await switch_entity.async_turn_off()
            else:
                _LOGGER.error(
                    "Could not find switch entity %s (unique_id: %s)",
                    entity_id,
                    _entry.unique_id,
                )

    async def async_handle_charging_action(call: ServiceCall, action_name: str) -> None:
        """Handle charging station actions (refresh_status, soft_reset, hard_reset, unlock_connector, block, unblock)."""
        entity_ids = await service.async_extract_entity_ids(call)
        if not entity_ids:
            _LOGGER.error(
                "Action %s requires entity_id. Received call data: %s",
                action_name,
                call.data,
            )
            return
        for entity_id in entity_ids:
            resolved = _resolve_evcnet_entity(hass, entity_id, "switch")
            if not resolved:
                continue
            coordinator, _entry, switch_entity = resolved
            if not switch_entity:
                _LOGGER.error(
                    "Could not find switch entity %s (unique_id: %s)",
                    entity_id,
                    _entry.unique_id,
                )
                continue
            spot_id = getattr(switch_entity, "_spot_id", None)
            if spot_id is None:
                _LOGGER.error("Switch entity missing spot_id: %s", _entry.unique_id)
                continue
            spot_data = coordinator.data.get(spot_id, {})
            spot_info = spot_data.get("info", {})
            channel_override = getattr(switch_entity, "_channel_override", None)
            channel = str(channel_override or spot_info.get("CHANNEL", "1"))
            try:
                _LOGGER.info(
                    "Performing %s on spot %s%s",
                    action_name,
                    spot_id,
                    f", channel {channel}" if action_name != "refresh_status" else "",
                )
                if action_name == "refresh_status":
                    await coordinator.client.get_status(spot_id)
                elif action_name == "soft_reset":
                    await coordinator.client.soft_reset(spot_id, channel)
                elif action_name == "hard_reset":
                    await coordinator.client.hard_reset(spot_id, channel)
                elif action_name == "unlock_connector":
                    await coordinator.client.unlock_connector(spot_id, channel)
                elif action_name == "block":
                    await coordinator.client.block(spot_id, channel)
                elif action_name == "unblock":
                    await coordinator.client.unblock(spot_id, channel)
                await asyncio.sleep(ACTION_SETTLE_DELAY_SEC)
                await coordinator.async_request_refresh()
            except Exception as err:
                _LOGGER.error(
                    "Failed to perform %s: %s", action_name, err, exc_info=True
                )
                await coordinator.async_request_refresh()

    def _charging_action_handler(action_name: str):
        """Return an async service handler that awaits async_handle_charging_action."""
        async def handler(call: ServiceCall) -> None:
            await async_handle_charging_action(call, action_name)
        return handler

    # Register the actions - Home Assistant will load schema from services.yaml
    hass.services.async_register(
        DOMAIN,
        "start_charging",
        async_handle_start_charging,
    )

    hass.services.async_register(
        DOMAIN,
        "stop_charging",
        async_handle_stop_charging,
    )

    hass.services.async_register(
        DOMAIN,
        "refresh_status",
        _charging_action_handler("refresh_status"),
    )

    hass.services.async_register(
        DOMAIN,
        "soft_reset",
        _charging_action_handler("soft_reset"),
    )

    hass.services.async_register(
        DOMAIN,
        "hard_reset",
        _charging_action_handler("hard_reset"),
    )

    hass.services.async_register(
        DOMAIN,
        "unlock_connector",
        _charging_action_handler("unlock_connector"),
    )

    hass.services.async_register(
        DOMAIN,
        "block",
        _charging_action_handler("block"),
    )

    hass.services.async_register(
        DOMAIN,
        "unblock",
        _charging_action_handler("unblock"),
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
