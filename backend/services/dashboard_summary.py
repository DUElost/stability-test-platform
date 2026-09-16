# -*- coding: utf-8 -*-
"""Dashboard summary aggregation and DEVICE_UPDATE materiality (#2324).

REST ``/stats/dashboard-summary`` and the WS ``dashboard_summary`` push share
``compute_dashboard_summary``. Device fan-out is gated by
``device_update_is_material`` so heartbeat noise does not storm the UI.
"""
from __future__ import annotations

import json
import math
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

# Alert thresholds mirrored by dashboard device/alert buckets.
LOW_BATTERY_THRESHOLD = 20
HIGH_TEMP_THRESHOLD = 45


def _disk_usage_percent_from_extra(extra: dict) -> Optional[float]:
    """Heartbeat extra.disk_usage.usage_percent；读盘失败 / 非有限 0–100 为 None。"""
    blob = extra.get("disk_usage")
    if not isinstance(blob, dict):
        return None
    raw = blob.get("usage_percent")
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value) or not 0.0 <= value <= 100.0:
        return None
    return value


def _low_battery(level: Any) -> bool:
    return level is not None and level < LOW_BATTERY_THRESHOLD


def _high_temp(temp: Any) -> bool:
    return temp is not None and temp > HIGH_TEMP_THRESHOLD


def device_update_is_material(
    *,
    prev_status: Any,
    new_status: Any,
    prev_adb_state: Any,
    new_adb_state: Any,
    prev_adb_connected: Any,
    new_adb_connected: Any,
    prev_battery_level: Any,
    new_battery_level: Any,
    prev_temperature: Any,
    new_temperature: Any,
) -> bool:
    """True only when status/adb or alert-threshold battery/temp buckets change.

    Battery/temp are material solely when the alert boolean flips
    (``battery < 20`` / ``temperature > 45``), not on every numeric tick.
    """
    if prev_status != new_status:
        return True
    if prev_adb_state != new_adb_state:
        return True
    if bool(prev_adb_connected) != bool(new_adb_connected):
        return True
    if _low_battery(prev_battery_level) != _low_battery(new_battery_level):
        return True
    if _high_temp(prev_temperature) != _high_temp(new_temperature):
        return True
    return False


def compute_dashboard_summary(db: Session) -> dict:
    """Aggregate host/device/alert buckets for dashboard (excludes retired hosts).

    ADR-0038 D5：仪表板「在线容量」口径排除退役主机。
    """
    hosts = db.execute(text("""
        SELECT status, extra, ip
        FROM host
        WHERE retired_at IS NULL
    """)).fetchall()
    devices = db.execute(text("""
        SELECT status, battery_level, temperature
        FROM device
    """)).fetchall()

    host_total = len(hosts)
    host_online = sum(1 for row in hosts if row.status == "ONLINE")
    host_offline = sum(1 for row in hosts if row.status == "OFFLINE")
    host_degraded = sum(1 for row in hosts if row.status == "DEGRADED")

    cpu_values: list[float] = []
    ram_values: list[float] = []
    disk_values: list[float] = []
    resource_points: list[dict] = []
    for row in hosts:
        raw = row.extra or {}
        # raw SQL on SQLite returns JSON strings; PostgreSQL returns dicts
        if isinstance(raw, str):
            try:
                extra = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                extra = {}
        else:
            extra = raw
        cpu = float(extra.get("cpu_load") or 0)
        ram = float(extra.get("ram_usage") or 0)
        disk = _disk_usage_percent_from_extra(extra)
        cpu_values.append(cpu)
        ram_values.append(ram)
        if disk is not None:
            disk_values.append(disk)
        if row.ip:
            resource_points.append({
                "ip": row.ip,
                "cpu_load": cpu,
                "ram_usage": ram,
                "disk_usage": disk,
            })

    idle = sum(1 for row in devices if row.status == "ONLINE")
    testing = sum(1 for row in devices if row.status == "BUSY")
    offline = sum(1 for row in devices if row.status == "OFFLINE")
    error = sum(1 for row in devices if row.status == "ERROR")
    low_battery = sum(
        1 for row in devices
        if row.battery_level is not None and row.battery_level < LOW_BATTERY_THRESHOLD
    )
    high_temp = sum(
        1 for row in devices
        if row.temperature is not None and row.temperature > HIGH_TEMP_THRESHOLD
    )

    return {
        "hosts": {
            "total": host_total,
            "online": host_online,
            "offline": host_offline,
            "degraded": host_degraded,
            "avg_cpu_load": round(sum(cpu_values) / host_total, 2) if host_total else 0.0,
            "avg_ram_usage": round(sum(ram_values) / host_total, 2) if host_total else 0.0,
            "avg_disk_usage": (
                round(sum(disk_values) / len(disk_values), 2) if disk_values else None
            ),
            "online_rate": round(host_online / host_total, 4) if host_total else 0.0,
        },
        "devices": {
            "total": len(devices),
            "idle": idle,
            "testing": testing,
            "offline": offline,
            "error": error,
            "low_battery": low_battery,
            "high_temp": high_temp,
        },
        "alerts": {
            "total": low_battery + high_temp + error,
            "low_battery": low_battery,
            "high_temp": high_temp,
            "error": error,
        },
        "host_resources": sorted(resource_points, key=lambda item: item["ip"])[:12],
    }
