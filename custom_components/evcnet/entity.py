"""Base entity for EVC-net."""

from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import EvcNetCoordinator


class EvcNetEntity(CoordinatorEntity[EvcNetCoordinator]):
    """Defines an EVC-net entity."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: EvcNetCoordinator, spot_id: str) -> None:
        """Initialize base entity."""
        super().__init__(coordinator)
        self._spot_id = spot_id

    @property
    def device_info(self):
        """Connect the entity to its spot device."""
        return self.coordinator.get_device_info(self._spot_id)
