"""The EVC-net integration."""
import asyncio
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import (
    CONF_PASSWORD,
    CONF_USERNAME,
    EVENT_HOMEASSISTANT_CLOSE,
    Platform,
)
from homeassistant.components import persistent_notification
from homeassistant.core import Event, HomeAssistant, ServiceCall, callback
from homeassistant.helpers import (
    config_validation as cv,
    entity_registry as er,
    issue_registry as ir,
    service,
)
from homeassistant.helpers.aiohttp_client import async_create_clientsession
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

# Keys in hass.data[DOMAIN] that are not config entry ids
DATA_CLIENTS = "_clients"        # entry_id -> EvcNetApiClient (survives reloads/retries)
DATA_MFA_CLIENT = "mfa_client"   # the client the submit_mfa action talks to

# This integration can only be set up from config entries, not from YAML
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

# Repair issue (with a form to enter the code) and notification shown when
# EVC-net asks for an MFA code
MFA_ISSUE_PREFIX = "mfa_required_"
MFA_NOTIFICATION_PREFIX = f"{DOMAIN}_mfa_"


@callback
def _async_show_mfa_request(hass: HomeAssistant, entry_id: str) -> None:
    """Show a notification and a repair with a code form."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        f"{MFA_ISSUE_PREFIX}{entry_id}",
        is_fixable=True,
        is_persistent=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="mfa_required",
        data={"entry_id": entry_id},
    )
    if (hass.config.language or "").lower().startswith("nl"):
        title = "EVC-net: verificatiecode nodig"
        message = (
            "EVC-net heeft je een verificatiecode gestuurd. Ga naar "
            "[Instellingen → Reparaties](/config/repairs), kies "
            "**EVC-net: verificatiecode nodig** en vul de code in.\n\n"
            "De code is ongeveer 15 minuten geldig."
        )
    else:
        title = "EVC-net: verification code needed"
        message = (
            "EVC-net has sent you a verification code. Go to "
            "[Settings → Repairs](/config/repairs), choose "
            "**EVC-net: verification code needed** and enter the code.\n\n"
            "The code is valid for about 15 minutes."
        )
    persistent_notification.async_create(
        hass,
        message,
        title=title,
        notification_id=f"{MFA_NOTIFICATION_PREFIX}{entry_id}",
    )


@callback
def _async_clear_mfa_request(hass: HomeAssistant, entry_id: str) -> None:
    """Remove the MFA notification and repair."""
    ir.async_delete_issue(hass, DOMAIN, f"{MFA_ISSUE_PREFIX}{entry_id}")
    persistent_notification.async_dismiss(hass, f"{MFA_NOTIFICATION_PREFIX}{entry_id}")


async def async_submit_mfa_code(
    hass: HomeAssistant, code: str, entry_id: str | None = None
) -> bool:
    """Send an MFA code to EVC-net. Returns True when it was accepted.

    Used by the evcnet.submit_mfa action and by the repair form.
    """
    domain_data = hass.data.get(DOMAIN, {})
    if entry_id is not None:
        client = domain_data.get(DATA_CLIENTS, {}).get(entry_id)
    else:
        client = domain_data.get(DATA_MFA_CLIENT)
    if client is None:
        _LOGGER.error("submit_mfa: no client found (has the integration been set up?)")
        return False

    try:
        result = await client.submit_mfa_code(code)
    except Exception:  # noqa: BLE001 - shown to the user as "code not accepted"
        _LOGGER.exception("submit_mfa: submitting the code failed")
        return False

    if not result:
        return False

    # Authenticated now: refresh a running coordinator, or retry setup.
    # A reload reuses the same (now authenticated) client, see async_setup_entry.
    for entry in hass.config_entries.async_entries(DOMAIN):
        coordinator = domain_data.get(entry.entry_id)
        if entry.state is ConfigEntryState.LOADED and coordinator is not None:
            await coordinator.async_request_refresh()
        else:
            await hass.config_entries.async_reload(entry.entry_id)
    return True


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

    async def async_handle_submit_mfa(call: ServiceCall) -> None:
        """Submit an MFA (OTP) code on the same client the coordinator uses."""
        code = str(call.data.get("code", "")).strip()
        if not code:
            _LOGGER.error("submit_mfa: no code received. Use: data: {code: \"123456\"}")
            return
        await async_submit_mfa_code(hass, code)

    # Register the actions - Home Assistant will load schema from services.yaml
    hass.services.async_register(DOMAIN, "start_charging", async_handle_start_charging)
    hass.services.async_register(DOMAIN, "stop_charging", async_handle_stop_charging)
    for action in (
        "refresh_status",
        "soft_reset",
        "hard_reset",
        "unlock_connector",
        "block",
        "unblock",
    ):
        hass.services.async_register(DOMAIN, action, _charging_action_handler(action))
    hass.services.async_register(DOMAIN, "submit_mfa", async_handle_submit_mfa)

    async def _async_close_sessions(_event: Event) -> None:
        """Close the EVC-net connections when Home Assistant stops."""
        for client in hass.data.get(DOMAIN, {}).get(DATA_CLIENTS, {}).values():
            if not client.session.closed:
                await client.session.close()

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_CLOSE, _async_close_sessions)

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
            missing_keys,
        )
        return False

    domain_data = hass.data.setdefault(DOMAIN, {})
    clients = domain_data.setdefault(DATA_CLIENTS, {})

    # Reuse the client across setup retries and reloads. Otherwise every retry
    # creates a fresh client and an MFA login done on the old one is lost.
    client = clients.get(entry.entry_id)
    if client is None or client.session.closed:
        # Own session (own cookie jar) so EVC-net login cookies don't mix with other
        # integrations. auto_cleanup=False: otherwise Home Assistant closes the session
        # as soon as a setup attempt fails (e.g. "MFA required"), and submit_mfa then
        # has no working connection. It is closed on remove / shutdown instead.
        session = async_create_clientsession(hass, auto_cleanup=False)
        client = EvcNetApiClient(
            entry.data[CONF_BASE_URL],
            entry.data[CONF_USERNAME],
            entry.data[CONF_PASSWORD],
            session,
        )
        clients[entry.entry_id] = client

    domain_data[DATA_MFA_CLIENT] = client

    # Show / clear the "enter your code" notification and repair form
    entry_id = entry.entry_id
    client.on_mfa_required = lambda: _async_show_mfa_request(hass, entry_id)
    client.on_authenticated = lambda: _async_clear_mfa_request(hass, entry_id)

    # Read max channels from options; default to DEFAULT_MAX_CHANNELS
    max_channels = int(entry.options.get(CONF_MAX_CHANNELS, DEFAULT_MAX_CHANNELS))
    coordinator = EvcNetCoordinator(hass, client, max_channels=max_channels)

    # Fetch initial data
    await coordinator.async_config_entry_first_refresh()

    domain_data[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Listen for options updates
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    return True


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the config entry when it's updated."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry (the client is kept so a reload stays logged in)."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id, None)

    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Forget the client when the integration is deleted."""
    _async_clear_mfa_request(hass, entry.entry_id)
    domain_data = hass.data.get(DOMAIN, {})
    client = domain_data.get(DATA_CLIENTS, {}).pop(entry.entry_id, None)
    if client is None:
        return
    if domain_data.get(DATA_MFA_CLIENT) is client:
        domain_data.pop(DATA_MFA_CLIENT, None)
    if not client.session.closed:
        await client.session.close()
