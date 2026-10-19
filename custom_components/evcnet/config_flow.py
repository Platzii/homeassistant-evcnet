"""Config flow for EVC-net integration."""
from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import EvcNetApiClient, EvcNetConnectionError
from .const import CONF_CARD_ID, CONF_MAX_CHANNELS, DEFAULT_MAX_CHANNELS, DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)

STEP_CARD_DATA_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_CARD_ID, description={"suggested_value": ""}): str,
    }
)


class EvcNetConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for EVC-net."""

    VERSION = 2

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._user_input: dict[str, Any] = {}

    @staticmethod
    @config_entries.callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> config_entries.OptionsFlow:
        """Get the options flow for this handler."""
        return EvcNetOptionsFlowHandler()

    async def _async_validate(self, username: str, password: str) -> str | None:
        """Try to log in. Returns an error key, or None on success."""
        client = EvcNetApiClient(username, password, async_get_clientsession(self.hass))
        try:
            if await client.authenticate():
                return None
            return "invalid_auth"
        except EvcNetConnectionError:
            return "cannot_connect"
        except Exception:  # pylint: disable=broad-except
            _LOGGER.exception("Unexpected exception")
            return "unknown"

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            error = await self._async_validate(user_input[CONF_USERNAME], user_input[CONF_PASSWORD])
            if error:
                errors["base"] = error
            else:
                await self.async_set_unique_id(user_input[CONF_USERNAME].strip().lower())
                self._abort_if_unique_id_configured()
                self._user_input = user_input
                return await self.async_step_card_config()

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_card_config(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the (optional) card ID configuration step."""
        if user_input is not None:
            data = {**self._user_input, **user_input}
            if not data.get(CONF_CARD_ID):
                data.pop(CONF_CARD_ID, None)

            return self.async_create_entry(
                title=f"EVC-net ({self._user_input[CONF_USERNAME]})",
                data=data,
            )

        return self.async_show_form(
            step_id="card_config",
            data_schema=STEP_CARD_DATA_SCHEMA,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> FlowResult:
        """Handle re-authentication when the stored credentials are rejected."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Ask for a new password."""
        errors: dict[str, str] = {}
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        if not entry:
            return self.async_abort(reason="unknown")

        if user_input is not None:
            error = await self._async_validate(entry.data[CONF_USERNAME], user_input[CONF_PASSWORD])
            if error:
                errors["base"] = error
            else:
                self.hass.config_entries.async_update_entry(
                    entry, data={**entry.data, CONF_PASSWORD: user_input[CONF_PASSWORD]}
                )
                await self.hass.config_entries.async_reload(entry.entry_id)
                return self.async_abort(reason="reauth_successful")

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            errors=errors,
            description_placeholders={"username": entry.data[CONF_USERNAME]},
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle reconfigure flow."""
        errors: dict[str, str] = {}

        config_entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        if not config_entry:
            _LOGGER.error("Config entry not found for reconfigure flow")
            return self.async_abort(reason="unknown")

        if user_input is not None:
            # Use existing password if not provided
            password = user_input.get(CONF_PASSWORD) or config_entry.data[CONF_PASSWORD]
            error = await self._async_validate(user_input[CONF_USERNAME], password)
            if error:
                errors["base"] = error
            else:
                self.hass.config_entries.async_update_entry(
                    config_entry,
                    data={
                        **config_entry.data,
                        CONF_USERNAME: user_input[CONF_USERNAME],
                        CONF_PASSWORD: password,
                    },
                )
                await self.hass.config_entries.async_reload(config_entry.entry_id)
                return self.async_abort(reason="reconfigure_successful")

        reconfigure_schema = vol.Schema(
            {
                vol.Required(CONF_USERNAME, default=config_entry.data.get(CONF_USERNAME)): str,
                vol.Optional(CONF_PASSWORD, default=""): str,  # Optional - leave blank to keep current
            }
        )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=reconfigure_schema,
            errors=errors,
        )


class EvcNetOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow for EVC-net."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current_options = dict(self.config_entry.options)
        options_schema = vol.Schema(
            {
                vol.Optional(
                    CONF_CARD_ID,
                    default=current_options.get(CONF_CARD_ID, self.config_entry.data.get(CONF_CARD_ID, "")),
                ): str,
                vol.Optional(
                    CONF_MAX_CHANNELS,
                    default=current_options.get(CONF_MAX_CHANNELS, DEFAULT_MAX_CHANNELS),
                ): vol.Coerce(int),
            }
        )

        return self.async_show_form(
            step_id="init",
            data_schema=options_schema,
        )
