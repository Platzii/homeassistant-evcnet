"""Pure helper tests for sensor parsing."""
import pytest

from custom_components.evcnet.sensor import convert_time_to_decimal_hours


@pytest.mark.parametrize("value,expected", [
    ("2:30", 2.5), ("0:00", 0.0), ("1d03:09", 27.15), ("12d00:30", 288.5),
    ("", 0.0), (None, 0.0), ("garbage", 0.0),
])
def test_convert_time_to_decimal_hours(value, expected):
    assert convert_time_to_decimal_hours(value) == pytest.approx(expected)
