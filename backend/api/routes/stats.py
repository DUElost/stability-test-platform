# -*- coding: utf-8 -*-
"""
Stats API — time-series endpoints for Dashboard charts.
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.api.routes.auth import get_current_active_user, User
from backend.core.database import get_db

router = APIRouter(prefix="/api/v1/stats", tags=["stats"])
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class HourlyActivityPoint(BaseModel):
    hour: str
    started: int = 0
    completed: int = 0
    failed: int = 0


class ActivityResponse(BaseModel):
    points: List[HourlyActivityPoint]
    hours: int


class MetricPoint(BaseModel):
    timestamp: str
    battery_level: Optional[int] = None
    temperature: Optional[int] = None
    network_latency: Optional[float] = None
    cpu_usage: Optional[float] = None
    mem_used: Optional[int] = None


class DeviceMetricsResponse(BaseModel):
    device_id: int
    points: List[MetricPoint]
    hours: int


class DailyCompletionPoint(BaseModel):
    date: str
    passed: int = 0
    failed: int = 0


class CompletionTrendResponse(BaseModel):
    points: List[DailyCompletionPoint]
    days: int


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/activity", response_model=ActivityResponse)
def get_activity(
    hours: int = Query(24, ge=1, le=168),
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    """Hourly task-run activity over the past N hours."""
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=hours)
    dialect = db.bind.dialect.name if db.bind is not None else ""

    if dialect == "postgresql":
        rows = db.execute(text("""
            SELECT
                to_char(date_trunc('hour', started_at), 'YYYY-MM-DD"T"HH24:00:00') AS hour,
                status,
                COUNT(*) AS cnt
            FROM job_instance
            WHERE started_at >= :since AND started_at IS NOT NULL
            GROUP BY hour, status
        """), {"since": since}).fetchall()
    else:
        rows = db.execute(text("""
            SELECT
                strftime('%Y-%m-%dT%H:00:00', started_at) AS hour,
                status,
                COUNT(*) AS cnt
            FROM job_instance
            WHERE started_at >= :since AND started_at IS NOT NULL
            GROUP BY hour, status
        """), {"since": since}).fetchall()

    buckets: dict = {}
    for row in rows:
        h, status, cnt = row[0], row[1], row[2]
        if h not in buckets:
            buckets[h] = {"started": 0, "completed": 0, "failed": 0}
        buckets[h]["started"] += cnt
        if status == "COMPLETED":
            buckets[h]["completed"] += cnt
        elif status in ("FAILED", "ABORTED"):
            buckets[h]["failed"] += cnt

    # Fill empty hours
    points = []
    cursor = since.replace(minute=0, second=0, microsecond=0)
    while cursor <= now:
        key = cursor.strftime('%Y-%m-%dT%H:00:00')
        b = buckets.get(key, {})
        points.append(HourlyActivityPoint(
            hour=key,
            started=b.get("started", 0),
            completed=b.get("completed", 0),
            failed=b.get("failed", 0),
        ))
        cursor += timedelta(hours=1)

    return ActivityResponse(points=points, hours=hours)


@router.get("/device/{device_id}/metrics", response_model=DeviceMetricsResponse)
def get_device_metrics(
    device_id: int,
    hours: int = Query(24, ge=1, le=168),
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    """Device metric history — DeviceMetricSnapshot table deprecated; returns empty."""
    return DeviceMetricsResponse(device_id=device_id, points=[], hours=hours)


class DashboardHostSummary(BaseModel):
    total: int
    online: int
    offline: int
    degraded: int
    avg_cpu_load: float
    avg_ram_usage: float
    avg_disk_usage: float
    online_rate: float


class DashboardDeviceSummary(BaseModel):
    total: int
    idle: int
    testing: int
    offline: int
    error: int
    low_battery: int
    high_temp: int


class DashboardAlertSummary(BaseModel):
    total: int
    low_battery: int
    high_temp: int
    error: int


class DashboardHostResourcePoint(BaseModel):
    ip: str
    cpu_load: float
    ram_usage: float
    disk_usage: float


class DashboardSummaryResponse(BaseModel):
    hosts: DashboardHostSummary
    devices: DashboardDeviceSummary
    alerts: DashboardAlertSummary
    host_resources: List[DashboardHostResourcePoint]


@router.get("/dashboard-summary", response_model=DashboardSummaryResponse)
def get_dashboard_summary(
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    hosts = db.execute(text("""
        SELECT status, extra, ip
        FROM host
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
        disk = float((extra.get("disk_usage") or {}).get("usage_percent") or 0)
        cpu_values.append(cpu)
        ram_values.append(ram)
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
    low_battery = sum(1 for row in devices if row.battery_level is not None and row.battery_level < 20)
    high_temp = sum(1 for row in devices if row.temperature is not None and row.temperature > 45)

    return DashboardSummaryResponse(
        hosts=DashboardHostSummary(
            total=host_total,
            online=host_online,
            offline=host_offline,
            degraded=host_degraded,
            avg_cpu_load=round(sum(cpu_values) / host_total, 2) if host_total else 0.0,
            avg_ram_usage=round(sum(ram_values) / host_total, 2) if host_total else 0.0,
            avg_disk_usage=round(sum(disk_values) / host_total, 2) if host_total else 0.0,
            online_rate=round(host_online / host_total, 4) if host_total else 0.0,
        ),
        devices=DashboardDeviceSummary(
            total=len(devices),
            idle=idle,
            testing=testing,
            offline=offline,
            error=error,
            low_battery=low_battery,
            high_temp=high_temp,
        ),
        alerts=DashboardAlertSummary(
            total=low_battery + high_temp + error,
            low_battery=low_battery,
            high_temp=high_temp,
            error=error,
        ),
        host_resources=sorted(resource_points, key=lambda item: item["ip"])[:12],
    )


@router.get("/completion-trend", response_model=CompletionTrendResponse)
def get_completion_trend(
    days: int = Query(7, ge=1, le=90),
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    """Daily pass/fail counts over the past N days."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    dialect = db.bind.dialect.name if db.bind is not None else ""
    if dialect == "postgresql":
        rows = db.execute(text("""
            SELECT
                to_char(date_trunc('day', ended_at), 'YYYY-MM-DD') AS day,
                status,
                COUNT(*) AS cnt
            FROM job_instance
            WHERE ended_at >= :since
              AND ended_at IS NOT NULL
              AND status IN ('COMPLETED', 'FAILED', 'ABORTED')
            GROUP BY day, status
        """), {"since": since}).fetchall()
    else:
        rows = db.execute(text("""
            SELECT
                strftime('%Y-%m-%d', ended_at) AS day,
                status,
                COUNT(*) AS cnt
            FROM job_instance
            WHERE ended_at >= :since
              AND ended_at IS NOT NULL
              AND status IN ('COMPLETED', 'FAILED', 'ABORTED')
            GROUP BY day, status
        """), {"since": since}).fetchall()

    buckets: dict = {}
    for row in rows:
        d, status, cnt = row[0], row[1], row[2]
        if d not in buckets:
            buckets[d] = {"passed": 0, "failed": 0}
        if status == "COMPLETED":
            buckets[d]["passed"] += cnt
        elif status in ("FAILED", "ABORTED"):
            buckets[d]["failed"] += cnt

    # Fill empty days
    points = []
    cursor = since.date()
    end = datetime.now(timezone.utc).date()
    while cursor <= end:
        key = cursor.isoformat()
        b = buckets.get(key, {})
        points.append(DailyCompletionPoint(
            date=key,
            passed=b.get("passed", 0),
            failed=b.get("failed", 0),
        ))
        cursor += timedelta(days=1)

    return CompletionTrendResponse(points=points, days=days)
