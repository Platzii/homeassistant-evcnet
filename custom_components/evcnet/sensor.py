"""Sensor platform for EVC-net."""
from collections.abc import Callable
from dataclasses import dataclass
import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy, UnitOfPower, UnitOfTime, PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import EvcNetCoordinator

_LOGGER = logging.getLogger(__name__)


@dataclass
class EvcNetSensorEntityDescription(SensorEntityDescription):
    """Describes EVC-net sensor entity."""

    value_fn: Callable[[dict[str, Any]], Any] | None = None


def get_nested_value(data: dict, *keys: str, default: Any = None) -> Any:
    """Safely get a nested value from a dictionary."""
    current = data
    for key in keys:
        if isinstance(current, dict):
            current = current.get(key, default)
        elif isinstance(current, list) and len(current) > 0:
            # Handle array responses like {"0": [[{...}]]}
            # Check if key is numeric (as string or int)
            try:
                idx = int(key)
                if idx < len(current):
                    current = current[idx]
                else:
                    return default
            except (ValueError, TypeError):
                # If it's not a numeric key, try to access the first element
                current = current[0] if len(current) > 0 else default
                if isinstance(current, dict):
                    current = current.get(key, default)
        else:
            return default
    return current if current is not None else default


def parse_locale_number(value: Any, default: float = 0.0) -> float:
    """Parse a number from a string, handling locale-specific formatting.

    Handles both comma and point as decimal separators (e.g., '1,888' or '1.888').
    Assumes that '.' or ',' is always a decimal separator, not a thousands separator.
    """
    if value is None:
        return default

    # If already a number, return it
    if isinstance(value, (int, float)):
        return float(value)

    # If not a string, try to convert
    if not isinstance(value, str):
        try:
            return float(value)
        except (ValueError, TypeError):
            return default

    # Remove whitespace
    value = value.strip()
    if not value:
        return default

    try:
        # Try direct conversion first (handles standard format with point as decimal)
        return float(value)
    except ValueError:
        pass

    # Handle locale-specific formatting
    # Both comma and point are treated as decimal separators
    if ',' in value:
        # Comma is decimal separator (e.g., '1,888' -> 1.888)
        value = value.replace(',', '.')

    try:
        return float(value)
    except ValueError as err:
        _LOGGER.warning("Error parsing number '%s': %s", value, err)
        return default


def convert_time_to_decimal_hours(time_str: str) -> float:
    """Convert HH:MM time format to decimal hours (e.g., 2:30 -> 2.5)."""
    if not time_str or not isinstance(time_str, str):
        return 0.0

    try:
        # Split by colon and convert to integers
        parts = time_str.split(':')
        if len(parts) == 2:
            hours = int(parts[0])
            minutes = int(parts[1])
            # Convert to decimal hours: hours + (minutes / 60)
            return hours + (minutes / 60.0)
        else:
            _LOGGER.warning("Invalid time format: %s", time_str)
            return 0.0
    except (ValueError, TypeError) as err:
        _LOGGER.warning("Error converting time '%s' to decimal hours: %s", time_str, err)
        return 0.0


def convert_energy_to_kwh(value: float, unit: str) -> float:
    """Convert energy value from various units to kWh.

    Supports: Wh, kWh, MWh, GWh (case-insensitive).
    Returns the value in kWh.
    """
    unit_upper = unit.strip().upper()

    # Conversion factors to kWh
    conversion_factors = {
        "WH": 0.001,      # 1 Wh = 0.001 kWh
        "KWH": 1.0,       # 1 kWh = 1 kWh
        "MWH": 1000.0,    # 1 MWh = 1000 kWh
        "GWH": 1000000.0, # 1 GWh = 1000000 kWh
    }

    factor = conversion_factors.get(unit_upper, 1.0)
    if unit_upper not in conversion_factors:
        _LOGGER.warning("Unknown energy unit '%s', assuming kWh", unit)

    try:
        return float(value) * factor
    except (ValueError, TypeError) as err:
        _LOGGER.warning("Error converting energy value '%s' with unit '%s': %s", value, unit, err)
        return 0.0


def get_total_energy_usage_kwh(data: dict) -> float:
    """Extract total energy usage and convert to kWh, handling dynamic units."""
    number = get_nested_value(data, "total_energy_usage", 0, "number", default=0)
    unit = get_nested_value(data, "total_energy_usage", 0, "unit", default="kWh")

    # Parse the number if it's a string
    if isinstance(number, str):
        number = parse_locale_number(number, default=0.0)
    elif not isinstance(number, (int, float)):
        number = 0.0

    # Convert to kWh
    return convert_energy_to_kwh(number, unit)


SENSOR_TYPES: tuple[EvcNetSensorEntityDescription, ...] = (
    EvcNetSensorEntityDescription(
        key="status",
        name="Status",
        icon="mdi:ev-station",
        value_fn=lambda data: (
            get_nested_value(data, "status", 0, 0, "NOTIFICATION", default="Unknown")
        ),
    ),
    EvcNetSensorEntityDescription(
        key="status_code",
        name="Status Code",
        icon="mdi:information",
        value_fn=lambda data: (
            get_nested_value(data, "status", 0, 0, "STATUS", default="Unknown")
        ),
    ),
    EvcNetSensorEntityDescription(
        key="current_power",
        name="Current Power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:lightning-bolt",
        value_fn=lambda data: (
            get_nested_value(data, "status", 0, 0, "MOM_POWER_KW", default=0)
        ),
    ),
    EvcNetSensorEntityDescription(
        key="energy_usage",
        name="Total Energy",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:flash",
        value_fn=get_total_energy_usage_kwh,
    ),
    EvcNetSensorEntityDescription(
        key="session_energy",
        name="Session Energy",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL,
        icon="mdi:battery-charging",
        value_fn=lambda data: (
            get_nested_value(data, "status", 0, 0, "TRANS_ENERGY_DELIVERED_KWH", default=0)
        ),
    ),
    EvcNetSensorEntityDescription(
        key="session_time",
        name="Session Time",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.HOURS,
        icon="mdi:timer",
        value_fn=lambda data: convert_time_to_decimal_hours(
            get_nested_value(data, "status", 0, 0, "TRANSACTION_TIME_H_M", default="")
        ),
    ),
    EvcNetSensorEntityDescription(
        key="software_version",
        name="Software Version",
        icon="mdi:information",
        value_fn=lambda data: (
            get_nested_value(data, "info", "SOFTWARE_VERSION", default="Unknown")
        ),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up EVC-net sensors."""
    coordinator: EvcNetCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = []
    for spot_id in coordinator.data:
        for description in SENSOR_TYPES:
            entities.append(
                EvcNetSensor(
                    coordinator,
                    description,
                    spot_id,
                )
            )

    async_add_entities(entities)


class EvcNetSensor(CoordinatorEntity[EvcNetCoordinator], SensorEntity):
    """Representation of a EVC-net sensor."""

    entity_description: EvcNetSensorEntityDescription

    def __init__(
        self,
        coordinator: EvcNetCoordinator,
        description: EvcNetSensorEntityDescription,
        spot_id: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._spot_id = spot_id
        self._attr_unique_id = f"{spot_id}_{description.key}"

        # Get spot info from coordinator data
        spot_info = coordinator.data.get(spot_id, {}).get("info", {})

        # Use NAME field, or fallback to spot ID
        spot_name = spot_info.get("NAME")
        if not spot_name or spot_name.strip() == "":
            spot_name = f"Charge Spot {spot_id}"

        self._attr_name = f"{spot_name} {description.name}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, spot_id)},
            "name": spot_name,
            "manufacturer": "Last Mile Solutions",
            "model": "EVC-net Charging Station",
            "sw_version": spot_info.get("SOFTWARE_VERSION"),
        }

    @property
    def native_value(self) -> Any:
        """Return the state of the sensor."""
        spot_data = self.coordinator.data.get(self._spot_id, {})

        if self.entity_description.value_fn:
            try:
                value = self.entity_description.value_fn(spot_data)
                # Filter out empty strings
                if value == "":
                    return None

                # Parse locale-aware numbers for numeric sensors
                # Sensors with device_class and state_class expect numeric values
                if (self.entity_description.device_class is not None and
                    self.entity_description.state_class is not None and
                    isinstance(value, str)):
                    # This is a numeric sensor with a string value, parse it
                    parsed_value = parse_locale_number(value, default=0.0)
                    return parsed_value

                return value
            except (KeyError, TypeError, AttributeError) as err:
                _LOGGER.debug(
                    "Error getting value for %s: %s", self.entity_description.key, err
                )
                return None

        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        spot_data = self.coordinator.data.get(self._spot_id, {})
        spot_info = spot_data.get("info", {})

        attributes = {
            "spot_id": self._spot_id,
            "address": spot_info.get("ADDRESS"),
            "reference": spot_info.get("REFERENCE"),
            "cost_center": spot_info.get("COST_CENTER_NUMBER"),
            "network_type": spot_info.get("NETWORK_TYPE"),
            "channels": spot_info.get("CHANNEL"),
        }

        # Add transaction info if available
        if spot_info.get("TRANSACTION_TIME_H_M"):
            attributes["transaction_time"] = spot_info.get("TRANSACTION_TIME_H_M")

        if spot_info.get("CUSTOMERS_IDX"):
            attributes["customer_id"] = spot_info.get("CUSTOMERS_IDX")

        if spot_info.get("CUSTOMER_NAME"):
            attributes["customer_name"] = spot_info.get("CUSTOMER_NAME")

        # Remove None values
        return {k: v for k, v in attributes.items() if v is not None}
