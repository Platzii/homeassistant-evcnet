"""Sensor platform for EVC-net."""
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy, UnitOfPower, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import EvcNetCoordinator
from .entity import EvcNetEntity

_LOGGER = logging.getLogger(__name__)

# Human-readable names for transaction statuses reported by the API
TRANSACTION_STATUS_NAMES = {
    "charging": "Charging",
    "committed": "Completed",
}


@dataclass(frozen=True, kw_only=True)
class EvcNetSensorEntityDescription(SensorEntityDescription):
    """Describes EVC-net sensor entity.

    value_fn receives (spot_data, channel_data).
    """

    value_fn: Callable[[dict[str, Any], dict[str, Any]], Any]


def parse_datetime(value: str | None) -> datetime | None:
    """Parse an ISO date string from the API."""
    return dt_util.parse_datetime(value) if value else None


def format_local(value: str | None) -> str:
    """Format an ISO date string from the API as local 'YYYY-MM-DD HH:MM'."""
    parsed = parse_datetime(value)
    return dt_util.as_local(parsed).strftime("%Y-%m-%d %H:%M") if parsed else ""


def format_duration(seconds: Any) -> str:
    """Format seconds as H:MM."""
    if not isinstance(seconds, (int, float)):
        return ""
    minutes = int(seconds) // 60
    return f"{minutes // 60}:{minutes % 60:02d}"


def wh_to_kwh(value: Any) -> float:
    """Convert a Wh value from the API to kWh."""
    return round(float(value or 0) / 1000, 3)


def transaction_status(tx: dict[str, Any]) -> str | None:
    """Return a human-readable status of a transaction."""
    status = tx.get("transactionStatus")
    if not status:
        return None
    return TRANSACTION_STATUS_NAMES.get(status.lower(), status.capitalize())


def session_hours(tx: dict[str, Any] | None) -> float:
    """Return the duration (charging + idle) of a transaction in hours."""
    if not tx:
        return 0.0
    seconds = (tx.get("durationCharging") or 0) + (tx.get("durationIdle") or 0)
    return round(seconds / 3600, 2)


def latest_log_entry(channel_data: dict[str, Any]) -> dict[str, Any] | None:
    """Return the most recent transaction of a channel."""
    log = channel_data.get("log") or []
    return log[0] if log else None


def summarize_log_rows(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return simplified rows for frontend use."""
    return [
        {
            "start": entry.get("startDate"),
            "end": entry.get("endDate"),
            "status": transaction_status(entry),
            "energy_kwh": wh_to_kwh(entry.get("energyDelivered")),
            "charging_time": format_duration(entry.get("durationCharging")),
            "idle_time": format_duration(entry.get("durationIdle")),
            "card_id": entry.get("loyaltyCardIdentifier"),
            "amount": entry.get("totalAmount"),
            "currency": entry.get("currency"),
        }
        for entry in entries
    ]


def format_log_as_markdown(entries: list[dict[str, Any]], max_rows: int = 50) -> str:
    """Create a plain Markdown table of recent sessions."""
    rows = [
        "| Start | End | Status | Energy (kWh) | Charging | Idle |\n|---|---|---|---:|---|---|"
    ]
    for entry in entries[:max_rows]:
        rows.append(
            f"| {format_local(entry.get('startDate'))} "
            f"| {format_local(entry.get('endDate'))} "
            f"| {transaction_status(entry) or ''} "
            f"| {wh_to_kwh(entry.get('energyDelivered')):.2f} "
            f"| {format_duration(entry.get('durationCharging'))} "
            f"| {format_duration(entry.get('durationIdle'))} |"
        )
    return "\n".join(rows)


def _channel(channel_data: dict[str, Any]) -> dict[str, Any]:
    return channel_data.get("channel") or {}


SENSOR_TYPES: tuple[EvcNetSensorEntityDescription, ...] = (
    EvcNetSensorEntityDescription(
        key="status",
        name="Status",
        icon="mdi:ev-station",
        value_fn=lambda spot, ch: _channel(ch).get("globalStatus"),
    ),
    EvcNetSensorEntityDescription(
        key="status_code",
        name="Status Code",
        icon="mdi:information",
        value_fn=lambda spot, ch: ", ".join(_channel(ch).get("statuses") or []) or None,
    ),
    EvcNetSensorEntityDescription(
        key="current_power",
        name="Current Power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:lightning-bolt",
        value_fn=lambda spot, ch: ch.get("power_kw"),
    ),
    EvcNetSensorEntityDescription(
        key="energy_usage",
        name="Total Energy",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:flash",
        value_fn=lambda spot, ch: spot.get("total_energy_kwh"),
    ),
    EvcNetSensorEntityDescription(
        key="session_energy",
        name="Session Energy",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:battery-charging",
        value_fn=lambda spot, ch: wh_to_kwh((ch.get("transaction") or {}).get("energyDelivered")),
    ),
    EvcNetSensorEntityDescription(
        key="session_time",
        name="Session Time",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.HOURS,
        icon="mdi:timer",
        value_fn=lambda spot, ch: session_hours(ch.get("transaction")),
    ),
    EvcNetSensorEntityDescription(
        key="software_version",
        name="Software Version",
        icon="mdi:information",
        value_fn=lambda spot, ch: (spot.get("info") or {}).get("softwareVersion"),
    ),
    EvcNetSensorEntityDescription(
        key="last_log_notification",
        name="Last Log Notification",
        icon="mdi:history",
        value_fn=lambda spot, ch: (entry := latest_log_entry(ch)) and transaction_status(entry),
    ),
    EvcNetSensorEntityDescription(
        key="last_log_time",
        name="Last Log Time",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:clock-outline",
        value_fn=lambda spot, ch: (entry := latest_log_entry(ch)) and parse_datetime(entry.get("startDate")),
    ),
    EvcNetSensorEntityDescription(
        key="log_summary",
        name="Log Summary",
        icon="mdi:table",
        value_fn=lambda spot, ch: len(ch.get("log") or []),
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
        detected_channels = coordinator.spot_channels.get(str(spot_id), 1)
        effective_max = max(coordinator.max_channels, detected_channels)

        # If only one channel, use legacy sensors (original naming)
        if effective_max == 1:
            entities.extend(EvcNetSensor(coordinator, description, spot_id) for description in SENSOR_TYPES)
        else:
            for ch in range(1, effective_max + 1):
                entities.extend(
                    EvcNetSensor(coordinator, description, spot_id, channel=ch) for description in SENSOR_TYPES
                )

    async_add_entities(entities)


class EvcNetSensor(EvcNetEntity, SensorEntity):
    """Representation of an EVC-net sensor (station-wide or for one channel)."""

    # Exclude large log attributes from Recorder (available at runtime for UI only)
    _unrecorded_attributes = frozenset({"log_entries", "log_rows", "log_markdown"})

    entity_description: EvcNetSensorEntityDescription

    def __init__(
        self,
        coordinator: EvcNetCoordinator,
        description: EvcNetSensorEntityDescription,
        spot_id: str,
        channel: int | None = None,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, spot_id, channel)
        self.entity_description = description
        if channel:
            self._attr_unique_id = f"{spot_id}_ch{channel}_{description.key}"
            self._attr_name = f"{self._spot_name} Ch {channel} {description.name}"
        else:
            self._attr_unique_id = f"{spot_id}_{description.key}"
            self._attr_name = f"{self._spot_name} {description.name}"

    @property
    def native_value(self) -> Any:
        """Return the state of the sensor."""
        try:
            value = self.entity_description.value_fn(self.spot_data, self.channel_data)
        except (KeyError, TypeError, ValueError, AttributeError) as err:
            _LOGGER.debug("Error getting value for %s: %s", self.entity_description.key, err)
            return None
        return None if value == "" else value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        info = self.spot_info
        channel = self.channel_data.get("channel") or {}
        attributes: dict[str, Any] = {
            "spot_id": self._spot_id,
            "channel": self._channel,
            "address": info.get("gpsAddress") or None,
            "city": info.get("gpsCity") or None,
            "reference": info.get("reference") or None,
            "evse_id": channel.get("evseId"),
            "authorization_mode": (info.get("accessOptions") or {}).get("authorizationMode"),
        }

        key = self.entity_description.key
        log_entries = self.channel_data.get("log") or []
        if key == "log_summary" and log_entries:
            # Only entity with full log data (for markdown card); not stored in Recorder
            attributes.update(
                {
                    "log_entries": log_entries,
                    "log_rows": summarize_log_rows(log_entries),
                    "log_markdown": format_log_as_markdown(log_entries),
                }
            )
        elif key.startswith("last_log") and log_entries:
            latest = log_entries[0]
            attributes.update(
                {
                    "log_transaction_id": latest.get("transactionId"),
                    "log_start": latest.get("startDate"),
                    "log_end": latest.get("endDate"),
                    "log_status": transaction_status(latest),
                    "log_energy_kwh": wh_to_kwh(latest.get("energyDelivered")),
                    "log_charging_time": format_duration(latest.get("durationCharging")),
                    "log_idle_time": format_duration(latest.get("durationIdle")),
                    "log_card_id": latest.get("loyaltyCardIdentifier"),
                }
            )
        elif key == "status":
            attributes.update(
                {
                    "statuses": channel.get("statuses"),
                    "last_status_change": channel.get("lastStatusChangeDate"),
                    "last_communication": info.get("lastCommunicationDate"),
                    "max_power_kw": (channel.get("power") or 0) / 1000 or None,
                    "connector_type": channel.get("connectorType"),
                }
            )

        return {k: v for k, v in attributes.items() if v is not None}
