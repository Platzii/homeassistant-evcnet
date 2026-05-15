"""Utils for EVC-net."""

import logging
import re
from typing import Any

_LOGGER = logging.getLogger(__name__)


def parse_locale_number(value: Any, default: float | None = 0.0) -> float | None:
    """Parseert getallen en gaat slim om met Europese/Engelse notaties."""
    if value is None or value == "":
        return default

    if isinstance(value, (int, float)):
        return float(value)

    # Delete currency markers and keep only numeric-relevant characters.
    clean_value = str(value).strip().replace("€", "").replace("%", "")
    clean_value = re.sub(r"\bEUR\b", "", clean_value, flags=re.IGNORECASE)
    clean_value = re.sub(r"[^0-9,.-]", "", clean_value)

    try:
        # Is it a standard float-string? (1.234)
        return float(clean_value)
    except ValueError:
        try:
            # Is the European format? (1.234,56)
            # Delete periods (thousands separator) and replace comma with dot
            normalized = clean_value.replace(".", "").replace(",", ".")
            return float(normalized)
        except ValueError:
            _LOGGER.warning("Unexpected number format for 50five: '%s'", value)
            return default


def convert_time_to_minutes(time_str: str) -> int:
    """Convert HH:mm to total minutes."""
    if not time_str or not isinstance(time_str, str):
        return 0
    try:
        parts = time_str.split(":")
        if len(parts) == 2:
            return (int(parts[0]) * 60) + int(parts[1])
    except (ValueError, IndexError):
        return 0
    else:
        return 0


def convert_energy_to_kwh(value: float, unit: str) -> float:
    """Convert energy value from various units to kWh.

    Supports: Wh, kWh, MWh, GWh (case-insensitive).
    Returns the value in kWh.
    """
    unit_upper = unit.strip().upper()

    # Conversion factors to kWh
    conversion_factors = {
        "WH": 0.001,  # 1 Wh = 0.001 kWh
        "KWH": 1.0,  # 1 kWh = 1 kWh
        "MWH": 1000.0,  # 1 MWh = 1000 kWh
        "GWH": 1000000.0,  # 1 GWh = 1000000 kWh
    }

    factor = conversion_factors.get(unit_upper, 1.0)
    if unit_upper not in conversion_factors:
        _LOGGER.warning("Unknown energy unit '%s', assuming kWh", unit)

    try:
        return float(value) * factor
    except (ValueError, TypeError) as err:
        _LOGGER.warning(
            "Error converting energy value '%s' with unit '%s': %s", value, unit, err
        )
        return 0.0


def get_total_energy_usage_kwh(data: dict) -> float:
    """Extract total energy usage and convert to kWh, handling dynamic units."""
    number = data.get("number", 0.0)
    unit = data.get("unit", "kWh")

    # Parse the number if it's a string
    if isinstance(number, str):
        number = parse_locale_number(number, default=0.0)
    elif not isinstance(number, (int, float)):
        number = 0.0

    # Convert to kWh
    return convert_energy_to_kwh(number, unit)
