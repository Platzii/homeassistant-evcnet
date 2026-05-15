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
from homeassistant.const import (
    CURRENCY_EURO,
    PERCENTAGE,
    EntityCategory,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import EvcNetConfigEntry
from .const import EvcNetException
from .coordinator import EvcNetCoordinator, EvcSpotData
from .entity import EvcNetEntity
from .utils import convert_time_to_minutes, parse_locale_number

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class EvcNetSensorEntityDescription(SensorEntityDescription):
    """Describes EVC-net sensor entity."""

    value_fn: Callable[[EvcSpotData], Any] | None = None
    attributes_fn: Callable[[EvcSpotData], dict[str, Any]] | None = None


def _session_energy_kwh(data: EvcSpotData) -> float | None:
    """Get session energy in kWh from active transaction or status fallback."""
    if data.active_transaction is not None:
        active_energy = parse_locale_number(
            data.active_transaction.get("energyDelivered"),
            default=None,
        )
        if active_energy is not None:
            return active_energy

    return parse_locale_number(
        data.status.get("TRANS_ENERGY_DELIVERED_KWH"),
        default=None,
    )


def _session_energy_source(data: EvcSpotData) -> str:
    """Determine source of session energy."""
    if data.active_transaction is not None:
        active_energy = parse_locale_number(
            data.active_transaction.get("energyDelivered"),
            default=None,
        )
        if active_energy is not None:
            return "active_transaction"
    return "status"


def _session_total_amount(data: EvcSpotData) -> float | None:
    """Get total amount from active transaction when available."""
    if data.active_transaction is None:
        return None

    return parse_locale_number(data.active_transaction.get("totalAmount"), default=None)


def _session_cost_source(data: EvcSpotData) -> str:
    """Determine source of session cost.
    
    Returns 'active_transaction' if using totalAmount from GraphQL,
    or 'calculated' if computing from energy * tariff.
    """
    if _session_total_amount(data) is not None:
        return "active_transaction"
    return "calculated"


def _session_cost_excl_vat(data: EvcSpotData) -> float | None:
    """Get session cost excluding VAT.

    Prefer GraphQL totalAmount/VAT when available to avoid discrepancies caused
    by differing energy units between APIs.
    """
    total_amount = _session_total_amount(data)
    if (
        total_amount is not None
        and data.vat_rate is not None
        and data.vat_rate >= 0
        and (1 + data.vat_rate) != 0
    ):
        return total_amount / (1 + data.vat_rate)

    session_energy = _session_energy_kwh(data)
    if session_energy is not None and data.hcc_tariff is not None:
        return session_energy * data.hcc_tariff

    return None


SENSOR_TYPES: tuple[EvcNetSensorEntityDescription, ...] = (
    EvcNetSensorEntityDescription(
        key="status",
        translation_key="status",
        value_fn=lambda data: data.status.get("NOTIFICATION", "Unknown"),
    ),
    EvcNetSensorEntityDescription(
        key="status_code",
        translation_key="status_code",
        value_fn=lambda data: data.status.get("STATUS", "Unknown"),
    ),
    EvcNetSensorEntityDescription(
        key="connector",
        translation_key="connector",
        value_fn=lambda data: data.status.get("CONNECTOR", "Unknown"),
        attributes_fn=lambda data: {
            "spot_id": data.info.get("IDX"),
            "channel": data.status.get("CHANNEL"),
            "card_idx": data.status.get("CARDS_IDX"),
            "customer_idx": data.status.get("CUSTOMERS_IDX"),
            "customer_name": data.status.get("CUSTOMER_NAME"),
            "software_version": data.info.get("SOFTWARE_VERSION"),
            "address": data.info.get("ADDRESS"),
            "reference": data.info.get("REFERENCE"),
            "cost_center": data.info.get("COST_CENTER_NUMBER"),
            "network_type": data.info.get("NETWORK_TYPE"),
        },
    ),
    EvcNetSensorEntityDescription(
        key="current_power",
        translation_key="current_power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: parse_locale_number(data.status.get("MOM_POWER_KW", 0.0)),
    ),
    EvcNetSensorEntityDescription(
        key="total_energy_usage",
        translation_key="total_energy_usage",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.total_energy_usage,
    ),
    EvcNetSensorEntityDescription(
        key="session_energy",
        translation_key="session_energy",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: parse_locale_number(
            data.status.get("TRANS_ENERGY_DELIVERED_KWH", 0.0)
        ),
    ),
    EvcNetSensorEntityDescription(
        key="session_time",
        translation_key="session_time",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: convert_time_to_minutes(
            data.status.get("TRANSACTION_TIME_H_M", "")
        ),
    ),
    EvcNetSensorEntityDescription(
        key="last_logging_update",
        translation_key="last_logging_update",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda data: dt_util.now(),
        attributes_fn=lambda data: {
            "entries": data.logging,
        },
    ),
    # --- HCC tariff & session cost sensors (GraphQL / mobile app backend) ---
    EvcNetSensorEntityDescription(
        key="reimbursement_tariff_excl_vat",
        translation_key="reimbursement_tariff_excl_vat",
        native_unit_of_measurement=f"{CURRENCY_EURO}/kWh",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.hcc_tariff,
        attributes_fn=lambda data: {
            "tariff_source": data.tariff_source,
            "web_tariff_raw": data.info.get("TARIFF"),
            "web_reimbursement_tariff_raw": data.info.get("REIMBURSEMENT_TARIFF"),
            "web_tariff_parsed": data.web_tariff,
            "web_reimbursement_tariff_parsed": data.web_reimbursement_tariff,
        },
    ),
    EvcNetSensorEntityDescription(
        key="reimbursement_tariff_incl_vat",
        translation_key="reimbursement_tariff_incl_vat",
        native_unit_of_measurement=f"{CURRENCY_EURO}/kWh",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: (
            round(data.hcc_tariff * (1 + data.vat_rate), 4)
            if data.hcc_tariff is not None and data.vat_rate is not None
            else None
        ),
        attributes_fn=lambda data: {
            "tariff_source": data.tariff_source,
            "vat_source": data.vat_source,
        },
    ),
    EvcNetSensorEntityDescription(
        key="vat_rate",
        translation_key="vat_rate",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: (
            round(data.vat_rate * 100, 1) if data.vat_rate is not None else None
        ),
        attributes_fn=lambda data: {
            "vat_source": data.vat_source or "unavailable",
            "active_transaction_source": data.active_transaction_source,
        },
    ),
    EvcNetSensorEntityDescription(
        key="session_cost_excl_vat",
        translation_key="session_cost_excl_vat",
        native_unit_of_measurement=CURRENCY_EURO,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: (
            round(_session_cost_excl_vat(data), 2)
            if _session_cost_excl_vat(data) is not None
            else None
        ),
        attributes_fn=lambda data: {
            "cost_source": _session_cost_source(data),
            "energy_source": _session_energy_source(data),
            "tariff_source": data.tariff_source,
            "vat_source": data.vat_source,
            "active_transaction_source": data.active_transaction_source,
        },
    ),
    EvcNetSensorEntityDescription(
        key="session_cost_incl_vat",
        translation_key="session_cost_incl_vat",
        native_unit_of_measurement=CURRENCY_EURO,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: (
            round(_session_total_amount(data), 2)
            if _session_total_amount(data) is not None
            else (
                round(
                    _session_energy_kwh(data) * data.hcc_tariff * (1 + data.vat_rate),
                    2,
                )
                if _session_energy_kwh(data) is not None
                and data.hcc_tariff is not None
                and data.vat_rate is not None
                else None
            )
        ),
        attributes_fn=lambda data: {
            "cost_source": _session_cost_source(data),
            "energy_source": _session_energy_source(data),
            "tariff_source": data.tariff_source,
            "vat_source": data.vat_source,
            "active_transaction_source": data.active_transaction_source,
        },
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EvcNetConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up EVC-net sensors."""
    coordinator = entry.runtime_data.coordinator

    async_add_entities(
        EvcNetSensor(
            coordinator,
            description,
            spot_id,
        )
        for spot_id in coordinator.data
        for description in SENSOR_TYPES
    )


class EvcNetSensor(EvcNetEntity, SensorEntity):
    """Representation of a EVC-net sensor."""

    entity_description: EvcNetSensorEntityDescription

    def __init__(
        self,
        coordinator: EvcNetCoordinator,
        description: EvcNetSensorEntityDescription,
        spot_id: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, spot_id)
        self.entity_description = description
        self._attr_unique_id = f"{spot_id}_{description.key}_sensor"

    @property
    def native_value(self) -> Any:
        """Return the state of the sensor."""
        spot_data: EvcSpotData | None = self.coordinator.data.get(self._spot_id)
        if spot_data is None or self.entity_description.value_fn is None:
            return None

        try:
            value = self.entity_description.value_fn(spot_data)
        except (KeyError, TypeError, AttributeError) as err:
            _LOGGER.warning(
                "Error getting value for %s at spot %s: %s",
                self.entity_description.key,
                self._spot_id,
                err,
            )
            return None

        return value

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return entity specific state attributes."""
        spot_data: EvcSpotData | None = self.coordinator.data.get(self._spot_id)

        if spot_data is None or self.entity_description.attributes_fn is None:
            return None

        try:
            return self.entity_description.attributes_fn(spot_data)
        except EvcNetException as err:
            _LOGGER.debug(
                "Error getting attributes for %s: %s", self.entity_description.key, err
            )
            return None
