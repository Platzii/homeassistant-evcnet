"""Config flow voor EVC-net."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import EvcNetApiClient
from .const import CONF_BASE_URL, DEFAULT_BASE_URL, DOMAIN

_LOGGER = logging.getLogger(__name__)

EVCNET_URL = "https://50five-snl.evc-net.com"

# Scheme for the user input during the config flow
STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_BASE_URL, default=DEFAULT_BASE_URL): str,
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


class EvcNetConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for EVC-net."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # Validate URL format
            if not user_input[CONF_BASE_URL].startswith(("http://", "https://")):
                errors["base_url"] = "invalid_url"

            if not errors:
                try:
                    # Test the connection
                    session = async_get_clientsession(self.hass)
                    client = EvcNetApiClient(
                        user_input[CONF_BASE_URL],
                        user_input[CONF_USERNAME],
                        user_input[CONF_PASSWORD],
                        session,
                    )

                    if not await client.authenticate():
                        errors["base"] = "invalid_auth"
                    else:
                        # Create unique ID based on username
                        await self.async_set_unique_id(
                            user_input[CONF_USERNAME].lower()
                        )
                        self._abort_if_unique_id_configured()

                        return self.async_create_entry(
                            title=user_input[CONF_USERNAME],
                            data=user_input,
                        )

                except Exception:  # pylint: disable=broad-except
                    _LOGGER.exception("Unexpected error during setup")
                    errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
            description_placeholders={"evcnet_url": EVCNET_URL},
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle reconfiguration if the password or URL changes."""
        return await self.async_step_user(user_input)
