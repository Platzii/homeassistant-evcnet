"""The EVC-net integration."""
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import EvcNetApiClient
from .const import CONF_BASE_URL, DOMAIN
from .coordinator import EvcNetCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.SWITCH]


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
