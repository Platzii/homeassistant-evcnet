"""Config, email OTP, reauthentication and options flows for EVC-net."""
import asyncio
import logging
from typing import Any
from urllib.parse import urlparse

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from .api import ApiError, AuthenticationError, InvalidOtp, TwoFactorRequired
from .const import (
    CONF_BASE_URL,
    CONF_MAX_CHANNELS,
    DEFAULT_BASE_URL,
    DEFAULT_MAX_CHANNELS,
    DOMAIN,
)
from .session import create_client

_LOGGER = logging.getLogger(__name__)
CONF_CARD_ID = "card_id"
CONF_CUSTOMER_ID = "customer_id"


def validate_url(url):
    try:
        parsed = urlparse(url)
        return (parsed.scheme == "https" and bool(parsed.hostname)
                and not parsed.username and not parsed.password
                and parsed.path in ("", "/") and not parsed.query and not parsed.fragment)
    except ValueError:
        return False


class EvcNetConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Keep the challenge cookies and CSRF token in memory until OTP succeeds."""
    VERSION = 1

    def __init__(self):
        self._user_input = {}
        self._client = None
        self._save = None
        self._entry = None
        self._mode = "user"

    @callback
    def async_remove(self):
        if self._client:
            self._client.session.detach()
        super().async_remove()

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return EvcNetOptionsFlowHandler()

    async def async_step_user(self, user_input=None):
        return await self._credentials("user", user_input)

    async def async_step_reconfigure(self, user_input=None):
        self._entry = self._get_reconfigure_entry()
        return await self._credentials("reconfigure", user_input)

    async def async_step_reauth(self, entry_data):
        self._entry = self._get_reauth_entry()
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        return await self._credentials("reauth_confirm", user_input)

    async def _credentials(self, step, user_input):
        self._mode = step
        errors = {}
        current = dict(self._entry.data) if self._entry else {}
        if user_input is not None:
            data = {**current, **user_input}
            if self._entry and not user_input.get(CONF_PASSWORD):
                data[CONF_PASSWORD] = current[CONF_PASSWORD]
            if not validate_url(data[CONF_BASE_URL]):
                errors["base"] = "invalid_url"
            else:
                data[CONF_BASE_URL] = data[CONF_BASE_URL].rstrip("/")
                self._user_input = data
                if not self._entry:
                    await self.async_set_unique_id(f"{data[CONF_USERNAME]}_{data[CONF_BASE_URL]}")
                    self._abort_if_unique_id_configured()
                if self._client:
                    self._client.session.detach()
                self._client, _, self._save = create_client(self.hass, data)
                try:
                    await self._client.authenticate()
                except TwoFactorRequired:
                    return await self.async_step_otp()
                except AuthenticationError:
                    errors["base"] = "invalid_auth"
                except (aiohttp.ClientError, asyncio.TimeoutError):
                    errors["base"] = "cannot_connect"
                except ApiError:
                    errors["base"] = "invalid_response"
                else:
                    return await self._finish_auth()
        # Reauth keeps account identity fixed; a blank password reuses the saved one.
        schema = {}
        if step != "reauth_confirm":
            schema[vol.Required(CONF_BASE_URL, default=current.get(CONF_BASE_URL, DEFAULT_BASE_URL))] = str
            schema[vol.Required(CONF_USERNAME, default=current.get(CONF_USERNAME, ""))] = str
        if self._entry:
            schema[vol.Optional(CONF_PASSWORD, default="")] = str
        else:
            schema[vol.Required(CONF_PASSWORD)] = str
        return self.async_show_form(step_id=step, data_schema=vol.Schema(schema), errors=errors)

    async def async_step_otp(self, user_input=None):
        errors = {}
        if self._client is None:
            return self.async_abort(reason="challenge_expired")
        if user_input is not None:
            try:
                await self._client.verify_otp(user_input["otp"])
            except InvalidOtp:
                errors["base"] = "invalid_otp"
            except AuthenticationError:
                # Let the user explicitly restart login, avoiding repeated email requests.
                return self.async_abort(reason="challenge_expired")
            except (aiohttp.ClientError, asyncio.TimeoutError):
                errors["base"] = "cannot_connect"
            except ApiError:
                errors["base"] = "invalid_response"
            else:
                return await self._finish_auth()
        return self.async_show_form(
            step_id="otp", data_schema=vol.Schema({vol.Required("otp"): str}), errors=errors)

    async def _finish_auth(self):
        # Neither the OTP nor the CSRF token is persisted.
        await self._save(self._client.export_cookies())
        self._client.session.detach()
        if self._entry:
            return self.async_update_reload_and_abort(
                self._entry, data_updates=self._user_input,
                reason="reauth_successful" if self._mode == "reauth_confirm" else "reconfigure_successful",
                reload_even_if_entry_is_unchanged=True,
            )
        return await self.async_step_card_config()

    async def async_step_card_config(self, user_input=None):
        if user_input is not None:
            data = {**self._user_input, **user_input}
            for key in (CONF_CARD_ID, CONF_CUSTOMER_ID):
                if not data.get(key):
                    data.pop(key, None)
            return self.async_create_entry(
                title=f"EVC-net ({data[CONF_USERNAME]})", data=data)
        return self.async_show_form(step_id="card_config", data_schema=vol.Schema({
            vol.Optional(CONF_CARD_ID): str, vol.Optional(CONF_CUSTOMER_ID): str}))


class EvcNetOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow for EVC-net."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options."""
        if user_input is not None:
            # Update the config entry with new options
            return self.async_create_entry(title="", data=user_input)

        # Create options schema with current values
        current_options = dict(self.config_entry.options)
        options_schema = vol.Schema(
            {
                vol.Optional(
                    CONF_CARD_ID,
                    default=self.config_entry.data.get(CONF_CARD_ID, ""),
                    description={"suggested_value": ""}
                ): str,
                vol.Optional(
                    CONF_CUSTOMER_ID,
                    default=self.config_entry.data.get(CONF_CUSTOMER_ID, ""),
                    description={"suggested_value": ""}
                ): str,
                vol.Optional(
                    CONF_MAX_CHANNELS,
                    default=current_options.get(CONF_MAX_CHANNELS, DEFAULT_MAX_CHANNELS),
                    description={"suggested_value": DEFAULT_MAX_CHANNELS}
                ): vol.Coerce(int),
            }
        )

        return self.async_show_form(
            step_id="init",
            data_schema=options_schema,
            description_placeholders={
                "info": (
                    "Update your RFID card ID and customer ID. "
                    "These are used to start charging sessions remotely. "
                    "Leave blank to use auto-detected values. "
                    "Set 'Max Channels' to create per-channel sensors and switches (keeps channel 1 names unchanged)."
                )
            },
        )
