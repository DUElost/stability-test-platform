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
from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from backend.api.routes.auth import get_current_active_user, User
from backend.core.database import get_db
from backend.core.legacy_aee import hidden_legacy_plan_ids

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


# ── Phase 2: 成功率/失败率细分 ──

class HostFailureRateItem(BaseModel):
    host_id: str
    hostname: str
    ip_address: Optional[str] = None
    total_jobs: int = 0
    failed: int = 0
    failure_rate: float = 0.0


class HostFailureRateResponse(BaseModel):
    items: List[HostFailureRateItem]
    days: int


class PlanSuccessRateItem(BaseModel):
    plan_id: int
    plan_name: str
    total_jobs: int = 0
    passed: int = 0
    failed: int = 0
    pass_rate: float = 0.0


class PlanSuccessRateResponse(BaseModel):
    items: List[PlanSuccessRateItem]
    days: int


class PlanRunPassRatePoint(BaseModel):
    date: str
    avg_pass_rate: float = 0.0
    run_count: int = 0


class PlanRunPassRateTrendResponse(BaseModel):
    points: List[PlanRunPassRatePoint]
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
    hidden_plan_ids = hidden_legacy_plan_ids(db)
    hidden_clause = ""
    params = {"since": since}
    if hidden_plan_ids:
        hidden_clause = " AND plan_id NOT IN :hidden_plan_ids"
        params["hidden_plan_ids"] = tuple(hidden_plan_ids)

    if dialect == "postgresql":
        stmt = text("""
            SELECT
                to_char(date_trunc('hour', started_at), 'YYYY-MM-DD"T"HH24:00:00') AS hour,
                status,
                COUNT(*) AS cnt
            FROM job_instance
            WHERE started_at >= :since AND started_at IS NOT NULL
        """ + hidden_clause + """
            GROUP BY hour, status
        """)
    else:
        stmt = text("""
            SELECT
                strftime('%Y-%m-%dT%H:00:00', started_at) AS hour,
                status,
                COUNT(*) AS cnt
            FROM job_instance
            WHERE started_at >= :since AND started_at IS NOT NULL
        """ + hidden_clause + """
            GROUP BY hour, status
        """)
    if hidden_plan_ids:
        stmt = stmt.bindparams(bindparam("hidden_plan_ids", expanding=True))
    rows = db.execute(stmt, params).fetchall()

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
    hidden_plan_ids = hidden_legacy_plan_ids(db)
    hidden_clause = ""
    params = {"since": since}
    if hidden_plan_ids:
        hidden_clause = " AND plan_id NOT IN :hidden_plan_ids"
        params["hidden_plan_ids"] = tuple(hidden_plan_ids)
    if dialect == "postgresql":
        stmt = text("""
            SELECT
                to_char(date_trunc('day', ended_at), 'YYYY-MM-DD') AS day,
                status,
                COUNT(*) AS cnt
            FROM job_instance
            WHERE ended_at >= :since
              AND ended_at IS NOT NULL
              AND status IN ('COMPLETED', 'FAILED', 'ABORTED')
        """ + hidden_clause + """
            GROUP BY day, status
        """)
    else:
        stmt = text("""
            SELECT
                strftime('%Y-%m-%d', ended_at) AS day,
                status,
                COUNT(*) AS cnt
            FROM job_instance
            WHERE ended_at >= :since
              AND ended_at IS NOT NULL
              AND status IN ('COMPLETED', 'FAILED', 'ABORTED')
        """ + hidden_clause + """
            GROUP BY day, status
        """)
    if hidden_plan_ids:
        stmt = stmt.bindparams(bindparam("hidden_plan_ids", expanding=True))
    rows = db.execute(stmt, params).fetchall()

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


# ═══════════════════════════════════════════════════════════════════════════
# Phase 2 — 成功率/失败率细分端点
# ═══════════════════════════════════════════════════════════════════════════


@router.get("/host-failure-rate", response_model=HostFailureRateResponse)
def get_host_failure_rate(
    days: int = Query(30, ge=1, le=90),
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    since = datetime.now(timezone.utc) - timedelta(days=days)
    hidden_plan_ids = hidden_legacy_plan_ids(db)
    hidden_clause = ""
    params = {"since": since}
    if hidden_plan_ids:
        hidden_clause = " AND j.plan_id NOT IN :hidden_plan_ids"
        params["hidden_plan_ids"] = tuple(hidden_plan_ids)

    stmt = text("""
        SELECT h.id, h.hostname, h.ip_address,
               COUNT(*) AS total_jobs,
               SUM(CASE WHEN j.status IN ('FAILED', 'ABORTED') THEN 1 ELSE 0 END) AS failed
        FROM job_instance j
        JOIN host h ON j.host_id = h.id
        WHERE j.started_at >= :since
    """ + hidden_clause + """
        GROUP BY h.id, h.hostname, h.ip_address
        HAVING COUNT(*) > 0
        ORDER BY SUM(CASE WHEN j.status IN ('FAILED', 'ABORTED') THEN 1 ELSE 0 END) * 1.0 / COUNT(*) DESC
    """)
    if hidden_plan_ids:
        stmt = stmt.bindparams(bindparam("hidden_plan_ids", expanding=True))
    rows = db.execute(stmt, params).fetchall()

    items: list[HostFailureRateItem] = []
    for row in rows:
        total = row[3]
        failed = row[4]
        items.append(HostFailureRateItem(
            host_id=row[0],
            hostname=row[1],
            ip_address=row[2],
            total_jobs=total,
            failed=failed,
            failure_rate=round(failed / total, 4) if total > 0 else 0.0,
        ))
    return HostFailureRateResponse(items=items[:limit], days=days)


@router.get("/plan-success-rate", response_model=PlanSuccessRateResponse)
def get_plan_success_rate(
    days: int = Query(30, ge=1, le=90),
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    since = datetime.now(timezone.utc) - timedelta(days=days)
    hidden_plan_ids = hidden_legacy_plan_ids(db)
    hidden_clause = ""
    params = {"since": since}
    if hidden_plan_ids:
        hidden_clause = " AND j.plan_id NOT IN :hidden_plan_ids"
        params["hidden_plan_ids"] = tuple(hidden_plan_ids)

    stmt = text("""
        SELECT p.id, p.name,
               COUNT(*) AS total_jobs,
               SUM(CASE WHEN j.status = 'COMPLETED' THEN 1 ELSE 0 END) AS passed,
               SUM(CASE WHEN j.status IN ('FAILED', 'ABORTED') THEN 1 ELSE 0 END) AS failed
        FROM job_instance j
        JOIN plan p ON j.plan_id = p.id
        WHERE j.started_at >= :since
    """ + hidden_clause + """
        GROUP BY p.id, p.name
        HAVING COUNT(*) > 0
        ORDER BY SUM(CASE WHEN j.status = 'COMPLETED' THEN 1 ELSE 0 END) * 1.0 / COUNT(*) DESC, total_jobs DESC
    """)
    if hidden_plan_ids:
        stmt = stmt.bindparams(bindparam("hidden_plan_ids", expanding=True))
    rows = db.execute(stmt, params).fetchall()

    items: list[PlanSuccessRateItem] = []
    for row in rows:
        total = row[2]
        passed = row[3]
        items.append(PlanSuccessRateItem(
            plan_id=row[0],
            plan_name=row[1],
            total_jobs=total,
            passed=passed,
            failed=row[4],
            pass_rate=round(passed / total, 4) if total > 0 else 0.0,
        ))
    return PlanSuccessRateResponse(items=items[:limit], days=days)


@router.get("/plan-run-pass-rate-trend", response_model=PlanRunPassRateTrendResponse)
def get_plan_run_pass_rate_trend(
    days: int = Query(30, ge=1, le=90),
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    since = datetime.now(timezone.utc) - timedelta(days=days)
    dialect = db.bind.dialect.name if db.bind is not None else ""

    if dialect == "postgresql":
        stmt = text("""
            SELECT
                to_char(date_trunc('day', pr.ended_at), 'YYYY-MM-DD') AS day,
                AVG(CASE WHEN rs.total > 0 THEN rs.completed::float / rs.total ELSE NULL END) AS avg_pass_rate,
                COUNT(*) AS run_count
            FROM (
                SELECT plan_run_id,
                       COUNT(*) AS total,
                       SUM(CASE WHEN status = 'COMPLETED' THEN 1 ELSE 0 END) AS completed
                FROM job_instance
                WHERE plan_run_id IN (
                    SELECT id FROM plan_run WHERE ended_at >= :since AND ended_at IS NOT NULL
                )
                GROUP BY plan_run_id
            ) rs
            JOIN plan_run pr ON rs.plan_run_id = pr.id
            WHERE pr.ended_at >= :since AND pr.ended_at IS NOT NULL
            GROUP BY date_trunc('day', pr.ended_at)
            ORDER BY day
        """)
    else:
        stmt = text("""
            SELECT
                date(pr.ended_at) AS day,
                AVG(CASE WHEN rs.total > 0 THEN CAST(rs.completed AS REAL) / rs.total ELSE NULL END) AS avg_pass_rate,
                COUNT(*) AS run_count
            FROM (
                SELECT plan_run_id,
                       COUNT(*) AS total,
                       SUM(CASE WHEN status = 'COMPLETED' THEN 1 ELSE 0 END) AS completed
                FROM job_instance
                WHERE plan_run_id IN (
                    SELECT id FROM plan_run WHERE ended_at >= :since AND ended_at IS NOT NULL
                )
                GROUP BY plan_run_id
            ) rs
            JOIN plan_run pr ON rs.plan_run_id = pr.id
            WHERE pr.ended_at >= :since AND pr.ended_at IS NOT NULL
            GROUP BY date(pr.ended_at)
            ORDER BY day
        """)

    rows = db.execute(stmt, {"since": since}).fetchall()

    buckets: dict[str, dict] = {}
    for row in rows:
        day_str = row[0]
        avg_pr = row[1]
        rc = row[2]
        if day_str:
            buckets[day_str] = {
                "avg_pass_rate": round(float(avg_pr), 4) if avg_pr is not None else 0.0,
                "run_count": int(rc),
            }

    points = []
    cursor = since.date()
    end = datetime.now(timezone.utc).date()
    while cursor <= end:
        key = cursor.isoformat()
        b = buckets.get(key, {})
        points.append(PlanRunPassRatePoint(
            date=key,
            avg_pass_rate=b.get("avg_pass_rate", 0.0),
            run_count=b.get("run_count", 0),
        ))
        cursor += timedelta(days=1)

    return PlanRunPassRateTrendResponse(points=points, days=days)
