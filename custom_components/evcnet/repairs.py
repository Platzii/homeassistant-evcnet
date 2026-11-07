"""Repair form to enter the EVC-net MFA (verification) code."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult

from . import MFA_ISSUE_PREFIX, async_submit_mfa_code

CODE_SCHEMA = vol.Schema({vol.Required("code"): str})


class EvcNetMfaRepairFlow(RepairsFlow):
    """Ask for the verification code that EVC-net sent."""

    def __init__(self, entry_id: str | None) -> None:
        """Remember which EVC-net login the code is for."""
        self._entry_id = entry_id

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Show the code field and send the code to EVC-net."""
        errors: dict[str, str] = {}

        if user_input is not None:
            code = str(user_input.get("code", "")).strip().replace(" ", "")
            if not code:
                errors["code"] = "empty_code"
            elif await async_submit_mfa_code(self.hass, code, self._entry_id):
                return self.async_create_entry(data={})
            else:
                errors["base"] = "invalid_code"

        return self.async_show_form(
            step_id="init", data_schema=CODE_SCHEMA, errors=errors
        )


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, Any] | None,
) -> RepairsFlow:
    """Create the repair flow for an MFA issue."""
    entry_id = (data or {}).get("entry_id")
    if entry_id is None and issue_id.startswith(MFA_ISSUE_PREFIX):
        entry_id = issue_id[len(MFA_ISSUE_PREFIX):]
    return EvcNetMfaRepairFlow(entry_id)
