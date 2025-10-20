"""Config flow for EVC-net integration."""
import logging
from typing import Any
from urllib.parse import urlparse

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import EvcNetApiClient
from .const import CONF_BASE_URL, DEFAULT_BASE_URL, DOMAIN

_LOGGER = logging.getLogger(__name__)

CONF_CARD_ID = "card_id"
CONF_CUSTOMER_ID = "customer_id"


def validate_url(url: str) -> bool:
    """Validate URL format."""
    try:
        result = urlparse(url)
        return all([result.scheme, result.netloc]) and result.scheme in ['http', 'https']
    except Exception:
        return False

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_BASE_URL, default=DEFAULT_BASE_URL): str,
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)

STEP_CARD_DATA_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_CARD_ID, description={"suggested_value": ""}): str,
        vol.Optional(CONF_CUSTOMER_ID, description={"suggested_value": ""}): str,
    }
)


class EvcNetConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for EVC-net."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._user_input: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # Validate the user input
            if not validate_url(user_input[CONF_BASE_URL]):
                errors["base"] = "invalid_url"
            else:
                try:
                    session = async_get_clientsession(self.hass)
                    client = EvcNetApiClient(
                        user_input[CONF_BASE_URL],
                        user_input[CONF_USERNAME],
                        user_input[CONF_PASSWORD],
                        session,
                    )

                    # Test authentication
                    if await client.authenticate():
                        # Set unique ID based on username and base URL
                        await self.async_set_unique_id(
                            f"{user_input[CONF_USERNAME]}_{user_input[CONF_BASE_URL]}"
                        )
                        self._abort_if_unique_id_configured()

                        # Store user input for next step
                        self._user_input = user_input

                        # Move to card ID configuration step
                        return await self.async_step_card_config()
                    else:
                        errors["base"] = "invalid_auth"
                except aiohttp.ClientError:
                    errors["base"] = "cannot_connect"
                except Exception:  # pylint: disable=broad-except
                    _LOGGER.exception("Unexpected exception")
                    errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
            description_placeholders={
                "info": "Enter your EVC-net account credentials"
            },
        )

    async def async_step_card_config(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the card ID configuration step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # Merge with previous input
            data = {**self._user_input, **user_input}

            # Clean up empty strings
            if not data.get(CONF_CARD_ID):
                data.pop(CONF_CARD_ID, None)
            if not data.get(CONF_CUSTOMER_ID):
                data.pop(CONF_CUSTOMER_ID, None)

            return self.async_create_entry(
                title=f"EVC-net ({self._user_input[CONF_USERNAME]})",
                data=data,
            )

        return self.async_show_form(
            step_id="card_config",
            data_schema=STEP_CARD_DATA_SCHEMA,
            errors=errors,
            description_placeholders={
                "info": (
                    "Optional: Provide your RFID card ID to enable starting charging sessions from Home Assistant. "
                    "You can find this by starting a charging session manually and checking the logs, "
                    "or leave blank and it will be auto-detected from your next charging session."
                )
            },
        )
