"""Select platform for EVC-net."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import EvcNetConfigEntry
from .coordinator import EvcNetCoordinator, EvcSpotData
from .entity import EvcNetEntity


@dataclass(frozen=True, kw_only=True)
class EvcNetSelectEntityDescription(SelectEntityDescription):
    """Describes EVC-net sensor entity."""

    options_fn: Callable[[EvcSpotData], Any] | None = None


SELECT_TYPES: tuple[EvcNetSelectEntityDescription, ...] = (
    EvcNetSelectEntityDescription(
        key="active_card",
        translation_key="active_card",
        entity_category=EntityCategory.CONFIG,
        options_fn=lambda data: list(data.available_cards.keys()),
    ),
    EvcNetSelectEntityDescription(
        key="active_channel",
        translation_key="active_channel",
        entity_category=EntityCategory.CONFIG,
        options_fn=lambda data: list(data.available_channels.values()),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EvcNetConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up EVC-net selects."""
    coordinator = entry.runtime_data.coordinator

    async_add_entities(
        EvcNetSelect(
            coordinator,
            description,
            spot_id,
        )
        for spot_id in coordinator.data
        for description in SELECT_TYPES
    )


class EvcNetSelect(EvcNetEntity, SelectEntity):
    """Representation of a EVC-net card selector."""

    entity_description: EvcNetSelectEntityDescription

    def __init__(
        self,
        coordinator: EvcNetCoordinator,
        description: EvcNetSelectEntityDescription,
        spot_id: str,
    ) -> None:
        """Initialize the select entity."""
        super().__init__(coordinator, spot_id)
        self.entity_description = description
        self._attr_unique_id = f"{spot_id}_{description.key}_select"

    @property
    def options(self) -> list[str]:
        """Return a list of available select options."""
        spot_data = self.coordinator.data.get(self._spot_id)
        if not spot_data or not self.entity_description.options_fn:
            return []

        return self.entity_description.options_fn(spot_data)

    @property
    def current_option(self) -> str | None:
        """Return the currently selected option."""
        spot_data = self.coordinator.data.get(self._spot_id)
        if not spot_data:
            return None

        if self.entity_description.key == "active_card":
            # Search name (key) for selected card_id (value)
            for name, card_id in spot_data.available_cards.items():
                if card_id == spot_data.selected_card_id:
                    return name
            return None

        if self.entity_description.key == "active_channel":
            return spot_data.selected_channel_id

        return None

    async def async_select_option(self, option: str) -> None:
        """Update the selected option in the coordinator data."""
        spot_data = self.coordinator.data.get(self._spot_id)
        if not spot_data:
            return

        if self.entity_description.key == "active_card":
            if selected_id := spot_data.available_cards.get(option):
                spot_data.selected_card_id = selected_id

        elif self.entity_description.key == "active_channel":
            spot_data.selected_channel_id = option
            await self.coordinator.async_poll_spot(self._spot_id)

        self.async_write_ha_state()
