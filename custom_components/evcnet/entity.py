"""Base entity for EVC-net."""
from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import EvcNetCoordinator


class EvcNetEntity(CoordinatorEntity[EvcNetCoordinator]):
    """Entity bound to one charge station (and optionally one channel)."""

    def __init__(self, coordinator: EvcNetCoordinator, spot_id: str, channel: int | None = None) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self._spot_id = spot_id
        self._channel = channel

        info = self.spot_info
        spot_name = (info.get("name") or "").strip() or f"Charge Spot {spot_id}"
        self._spot_name = spot_name
        manufacturer = info.get("manufacturerType") or {}
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, spot_id)},
            name=spot_name,
            manufacturer=manufacturer.get("vendor") or "Last Mile Solutions",
            model=manufacturer.get("model") or "EVC-net Charging Station",
            sw_version=info.get("softwareVersion"),
            serial_number=info.get("physicalNumber") or info.get("commId"),
        )

    @property
    def channel_no(self) -> int:
        """Return the channel number this entity acts on (1 when not channel-specific)."""
        return self._channel or 1

    @property
    def spot_data(self) -> dict[str, Any]:
        """Return the coordinator data for this station."""
        return (self.coordinator.data or {}).get(self._spot_id, {})

    @property
    def spot_info(self) -> dict[str, Any]:
        """Return the station info as returned by the API."""
        return self.spot_data.get("info", {})

    @property
    def channel_data(self) -> dict[str, Any]:
        """Return the coordinator data for this entity's channel."""
        return self.coordinator.get_channel(self._spot_id, self.channel_no)

    @property
    def channel_id(self) -> str | None:
        """Return the internal API id of this entity's channel."""
        return (self.channel_data.get("channel") or {}).get("id")

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return super().available and self._spot_id in (self.coordinator.data or {})
