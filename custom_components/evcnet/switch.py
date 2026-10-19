"""Switch platform for EVC-net."""
import asyncio
import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import ACTION_SETTLE_DELAY_SEC, CHANNEL_STATUS_TRANSACTION, CONF_CARD_ID, DOMAIN
from .coordinator import EvcNetCoordinator
from .entity import EvcNetEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up EVC-net switches."""
    coordinator: EvcNetCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = []
    for spot_id in coordinator.data:
        detected_channels = coordinator.spot_channels.get(str(spot_id), 1)
        effective_max = max(coordinator.max_channels, detected_channels)

        for ch in range(1, effective_max + 1):
            ch_switch = EvcNetChargingSwitch(coordinator, spot_id, entry, channel=ch)
            entities.append(ch_switch)
            coordinator.entities[ch_switch.unique_id] = ch_switch

    async_add_entities(entities)


class EvcNetChargingSwitch(EvcNetEntity, SwitchEntity):
    """Switch that starts/stops a charging session on one channel."""

    _attr_icon = "mdi:ev-station"

    def __init__(
        self,
        coordinator: EvcNetCoordinator,
        spot_id: str,
        entry: ConfigEntry,
        channel: int = 1,
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator, spot_id, channel)
        # Keep original unique ID and name for the primary channel
        if channel != 1:
            self._attr_unique_id = f"{spot_id}_ch{channel}_charging"
            self._attr_name = f"{self._spot_name} Ch {channel} Charging"
        else:
            self._attr_unique_id = f"{spot_id}_charging"
            self._attr_name = f"{self._spot_name} Charging"

        # Priority: 1) Options, 2) Config entry, 3) Card linked to the station / account
        self._configured_card_id = entry.options.get(CONF_CARD_ID) or entry.data.get(CONF_CARD_ID)

    @property
    def card_id(self) -> str | None:
        """Return the card used to start a session."""
        return self._configured_card_id or self.coordinator.default_card(self._spot_id)

    @property
    def is_on(self) -> bool:
        """Return true if a charging session is active on this channel."""
        if self.channel_data.get("transaction"):
            return True
        statuses = (self.channel_data.get("channel") or {}).get("statuses") or []
        return CHANNEL_STATUS_TRANSACTION in statuses

    async def _run(self, description: str, action) -> None:
        """Run an action, then refresh the coordinator."""
        try:
            await action()
            # Give the charging station time to process the command before refreshing
            await asyncio.sleep(ACTION_SETTLE_DELAY_SEC)
        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.error("Failed to %s: %s", description, err)
        await self.coordinator.async_request_refresh()
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Start charging."""
        card_id = kwargs.get("card_id") or self.card_id
        channel_id = self.channel_id
        if not channel_id:
            _LOGGER.error("Cannot start charging: channel %s of spot %s not found", self.channel_no, self._spot_id)
            return
        if not card_id:
            _LOGGER.error(
                "Cannot start charging on spot %s: no card available. "
                "Configure a card ID in the integration options or pass card_id to the action.",
                self._spot_id,
            )
            return

        _LOGGER.info("Starting charging on spot %s channel %s with card %s", self._spot_id, self.channel_no, card_id)
        await self._run(
            "start charging",
            lambda: self.coordinator.client.start_charging(self._spot_id, channel_id, card_id),
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Stop charging."""
        channel_id = self.channel_id
        if not channel_id:
            _LOGGER.error("Cannot stop charging: channel %s of spot %s not found", self.channel_no, self._spot_id)
            return

        _LOGGER.info("Stopping charging on spot %s channel %s", self._spot_id, self.channel_no)
        await self._run(
            "stop charging",
            lambda: self.coordinator.client.stop_charging(self._spot_id, channel_id),
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes for this channel."""
        channel = self.channel_data.get("channel") or {}
        transaction = self.channel_data.get("transaction") or {}
        attributes = {
            "spot_id": self._spot_id,
            "channel": self.channel_no,
            "channel_id": channel.get("id"),
            "status": channel.get("globalStatus"),
            "power_kw": self.channel_data.get("power_kw"),
            "transaction_id": transaction.get("transactionId"),
            "transaction_energy_kwh": (
                round(transaction["energyDelivered"] / 1000, 3) if transaction.get("energyDelivered") is not None else None
            ),
            "transaction_start": transaction.get("startDate"),
            "card_id": self.card_id,
        }
        return {k: v for k, v in attributes.items() if v is not None}
