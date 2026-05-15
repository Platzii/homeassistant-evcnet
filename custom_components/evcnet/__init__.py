"""The EVC-net integration."""

from dataclasses import dataclass
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv, entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import EvcNetApiClient
from .const import CONF_BASE_URL, DOMAIN, EvcNetException
from .coordinator import EvcNetCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    Platform.BUTTON,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]


@dataclass
class EvcNetData:
    """Define an object to hold EVC-net data."""

    coordinator: EvcNetCoordinator


type EvcNetConfigEntry = ConfigEntry[EvcNetData]

# This integration can only be set up from config entries, not from YAML
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup_entry(hass: HomeAssistant, entry: EvcNetConfigEntry) -> bool:
    """Set up EVC-net from a config entry."""

    # Validate required configuration
    for key in [CONF_BASE_URL, CONF_USERNAME, CONF_PASSWORD]:
        if key not in entry.data:
            _LOGGER.error(
                "Missing required configuration keys: %s. "
                "Please delete and re-add the integration: "
                "Settings > Devices & Services > EVC-net > Delete",
                key,
            )
            return False

    # Initialize API Client & Coordinator
    session = async_get_clientsession(hass)
    client = EvcNetApiClient(
        entry.data[CONF_BASE_URL],
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
        session,
    )

    coordinator = EvcNetCoordinator(hass, client)

    try:
        await coordinator.async_config_entry_first_refresh()
    except EvcNetException as err:
        raise ConfigEntryNotReady(f"Connection with EVC-net failed: {err}") from err

    entry.runtime_data = EvcNetData(coordinator=coordinator)

    # Register the services (only the first time the integration is set up)
    setup_services(hass)

    # Start platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


def setup_services(hass: HomeAssistant) -> None:
    """Register EVC-net services."""
    if hass.services.has_service(DOMAIN, "start_charging"):
        return

    async def handle_start_charging(call: ServiceCall) -> None:
        """Service to start a charging session (supports an optional RFID card ID)."""
        entity_ids = call.data.get("entity_id", [])
        card_id_override = call.data.get("card_id")
        channel_id_override = call.data.get("channel_id")

        registry = er.async_get(hass)
        for entity_id in entity_ids:
            entity_entry = registry.async_get(entity_id)
            if not entity_entry or not entity_entry.config_entry_id:
                continue

            # Haal de entry op via de registry
            entry: EvcNetConfigEntry | None = hass.config_entries.async_get_entry(
                entity_entry.config_entry_id
            )
            if not entry or not hasattr(entry, "runtime_data"):
                continue

            coordinator = entry.runtime_data.coordinator
            # Get spot_id from unique_id (bijv. "12345_charging_switch")
            spot_id = entity_entry.unique_id.split("_")[0]
            spot_data = coordinator.data.get(spot_id)

            if spot_data:
                # Prioriteit: 1. Service call card_id, 2. Geselecteerde kaart in UI
                card_id = card_id_override or spot_data.selected_card_id
                customer_id = spot_data.customer_id
                channel_id = channel_id_override or spot_data.selected_channel_id

                if card_id and channel_id and customer_id:
                    await coordinator.client.start_charging(
                        spot_id, customer_id, card_id, channel_id
                    )
                    await coordinator.async_request_refresh()

    hass.services.async_register(DOMAIN, "start_charging", handle_start_charging)


async def async_unload_entry(hass: HomeAssistant, entry: EvcNetConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
