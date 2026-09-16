# -*- coding: utf-8 -*-
"""
Stats API — time-series endpoints for Dashboard charts.
"""

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.api.routes.auth import get_current_active_user, require_admin, User
from backend.api.schemas.file_server import FileServerOverview
from backend.core.database import get_db
from backend.models.host import Host
from backend.services.file_server_monitor import collect_file_server_overview

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
    params = {"since": since}

    if dialect == "postgresql":
        stmt = text("""
            SELECT
                to_char(date_trunc('hour', started_at), 'YYYY-MM-DD"T"HH24:00:00') AS hour,
                status,
                COUNT(*) AS cnt
            FROM job_instance
            WHERE started_at >= :since AND started_at IS NOT NULL
        """
            """
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
        """
            """
            GROUP BY hour, status
        """)
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


class DashboardHostSummary(BaseModel):
    total: int
    online: int
    offline: int
    degraded: int
    avg_cpu_load: float
    avg_ram_usage: float
    avg_disk_usage: Optional[float] = None
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
    disk_usage: Optional[float] = None


class DashboardSummaryResponse(BaseModel):
    hosts: DashboardHostSummary
    devices: DashboardDeviceSummary
    alerts: DashboardAlertSummary
    host_resources: List[DashboardHostResourcePoint]


@router.get("/file-server", response_model=FileServerOverview)
def get_file_server_overview(
    hours: int = Query(6, ge=1, le=168),
    db: Session = Depends(get_db),
    _current_user: User = Depends(require_admin),
):
    """Return NFS capacity, node load, and active-Agent mount compliance.

    未设 ``STP_AEE_NFS_ROOT`` → 503（非 500），避免误报 STORAGE_NOT_MOUNTED。
    共享根是部署前置，与 ``backend/services/dedup_scan.py`` 同样依赖。
    """
    fresh_seconds = max(30, int(os.getenv("STP_FILE_SERVER_AGENT_FRESH_SECONDS", "180")))
    active_since = datetime.now(timezone.utc) - timedelta(seconds=fresh_seconds)
    # ADR-0038 D5：file-server 的「活跃 Agent」口径 = 在线且未退役——
    # 退役主机即使 Agent 仍在心跳也不计入挂载合规统计。
    active_hosts = (
        db.query(Host)
        .filter(
            Host.status == "ONLINE",
            Host.last_heartbeat.isnot(None),
            Host.last_heartbeat >= active_since,
            Host.retired_at.is_(None),
        )
        .all()
    )
    try:
        overview = collect_file_server_overview(active_hosts, hours=hours)
    except RuntimeError as exc:
        logger.warning("file_server_endpoint_root_not_configured err=%s", exc)
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return FileServerOverview.model_validate(overview)


@router.get("/dashboard-summary", response_model=DashboardSummaryResponse)
def get_dashboard_summary(
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    """Cold-start + slow safety net; live updates via WS dashboard_summary (#2324)."""
    from backend.services.dashboard_summary import compute_dashboard_summary

    return DashboardSummaryResponse.model_validate(compute_dashboard_summary(db))


@router.get("/completion-trend", response_model=CompletionTrendResponse)
def get_completion_trend(
    days: int = Query(7, ge=1, le=90),
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    """Daily pass/fail counts over the past N days."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    dialect = db.bind.dialect.name if db.bind is not None else ""
    params = {"since": since}
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
        """
            """
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
        """
            """
            GROUP BY day, status
        """)
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
    params = {"since": since}

    # ADR-0038 D5（裁决 D-5）：本口径**刻意不排除退役主机**——它是「历史
    # 失败率」KPI，退役前的执行历史仍然属于该主机的运维事实；退役只是
    # 「不再派发」，不是「抹掉历史」。与上面 file-server / dashboard 的
    # 「当前容量」口径差异是有意为之（列表/容量排除，历史统计保留）。
    stmt = text("""
        SELECT h.id, h.hostname, h.ip_address,
               COUNT(*) AS total_jobs,
               SUM(CASE WHEN j.status IN ('FAILED', 'ABORTED') THEN 1 ELSE 0 END) AS failed
        FROM job_instance j
        JOIN host h ON j.host_id = h.id
        WHERE j.started_at >= :since
    """
            """
        GROUP BY h.id, h.hostname, h.ip_address
        HAVING COUNT(*) > 0
        ORDER BY SUM(CASE WHEN j.status IN ('FAILED', 'ABORTED') THEN 1 ELSE 0 END) * 1.0 / COUNT(*) DESC
    """)
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
    params = {"since": since}

    stmt = text("""
        SELECT p.id, p.name,
               COUNT(*) AS total_jobs,
               SUM(CASE WHEN j.status = 'COMPLETED' THEN 1 ELSE 0 END) AS passed,
               SUM(CASE WHEN j.status IN ('FAILED', 'ABORTED') THEN 1 ELSE 0 END) AS failed
        FROM job_instance j
        JOIN plan p ON j.plan_id = p.id
        WHERE j.started_at >= :since
    """
            """
        GROUP BY p.id, p.name
        HAVING COUNT(*) > 0
        ORDER BY SUM(CASE WHEN j.status = 'COMPLETED' THEN 1 ELSE 0 END) * 1.0 / COUNT(*) DESC, total_jobs DESC
    """)
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
