"""Unit tests for dashboard summary materiality (#2324)."""
from __future__ import annotations

import pytest

from backend.services.dashboard_summary import device_update_is_material


def _base(**overrides):
    kwargs = dict(
        prev_status="ONLINE",
        new_status="ONLINE",
        prev_adb_state="device",
        new_adb_state="device",
        prev_adb_connected=True,
        new_adb_connected=True,
        prev_battery_level=50,
        new_battery_level=50,
        prev_temperature=30,
        new_temperature=30,
    )
    kwargs.update(overrides)
    return kwargs


@pytest.mark.parametrize(
    "overrides",
    [
        {"new_status": "OFFLINE"},
        {"new_adb_state": "unauthorized"},
        {"new_adb_connected": False},
        {"prev_battery_level": 25, "new_battery_level": 15},
        {"prev_battery_level": 15, "new_battery_level": 25},
        {"prev_temperature": 40, "new_temperature": 50},
        {"prev_temperature": 50, "new_temperature": 40},
    ],
)
def test_device_update_is_material_true(overrides):
    assert device_update_is_material(**_base(**overrides)) is True


@pytest.mark.parametrize(
    "overrides",
    [
        {},
        {"prev_battery_level": 50, "new_battery_level": 40},
        {"prev_battery_level": 15, "new_battery_level": 10},
        {"prev_temperature": 30, "new_temperature": 40},
        {"prev_temperature": 50, "new_temperature": 55},
        {"prev_adb_connected": 1, "new_adb_connected": True},
    ],
)
def test_device_update_is_material_false(overrides):
    assert device_update_is_material(**_base(**overrides)) is False
