# -*- coding: utf-8 -*-
"""
Stats API — time-series endpoints for Dashboard charts.
"""

import logging
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.core.database import get_db
from backend.models.schemas import DeviceMetricSnapshot, RunStatus

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
):
    """Hourly task-run activity over the past N hours."""
    now = datetime.utcnow()
    since = now - timedelta(hours=hours)

    rows = db.execute(text("""
        SELECT
            strftime('%Y-%m-%dT%H:00:00', started_at) AS hour,
            status,
            COUNT(*) AS cnt
        FROM task_runs
        WHERE started_at >= :since AND started_at IS NOT NULL
        GROUP BY hour, status
    """), {"since": since}).fetchall()

    buckets: dict = {}
    for row in rows:
        h, status, cnt = row[0], row[1], row[2]
        if h not in buckets:
            buckets[h] = {"started": 0, "completed": 0, "failed": 0}
        buckets[h]["started"] += cnt
        if status in (RunStatus.FINISHED.value, "FINISHED"):
            buckets[h]["completed"] += cnt
        elif status in (RunStatus.FAILED.value, "FAILED"):
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
):
    """Device metric history from snapshots."""
    since = datetime.utcnow() - timedelta(hours=hours)
    snapshots = (
        db.query(DeviceMetricSnapshot)
        .filter(
            DeviceMetricSnapshot.device_id == device_id,
            DeviceMetricSnapshot.timestamp >= since,
        )
        .order_by(DeviceMetricSnapshot.timestamp)
        .all()
    )
    # Downsample if too many points
    if len(snapshots) > 500:
        step = len(snapshots) // 500
        snapshots = snapshots[::step]

    points = [
        MetricPoint(
            timestamp=s.timestamp.isoformat(),
            battery_level=s.battery_level,
            temperature=s.temperature,
            network_latency=s.network_latency,
            cpu_usage=s.cpu_usage,
            mem_used=s.mem_used,
        )
        for s in snapshots
    ]
    return DeviceMetricsResponse(device_id=device_id, points=points, hours=hours)


@router.get("/completion-trend", response_model=CompletionTrendResponse)
def get_completion_trend(
    days: int = Query(7, ge=1, le=90),
    db: Session = Depends(get_db),
):
    """Daily pass/fail counts over the past N days."""
    since = datetime.utcnow() - timedelta(days=days)
    rows = db.execute(text("""
        SELECT
            strftime('%Y-%m-%d', finished_at) AS day,
            status,
            COUNT(*) AS cnt
        FROM task_runs
        WHERE finished_at >= :since
          AND finished_at IS NOT NULL
          AND status IN ('FINISHED', 'FAILED')
        GROUP BY day, status
    """), {"since": since}).fetchall()

    buckets: dict = {}
    for row in rows:
        d, status, cnt = row[0], row[1], row[2]
        if d not in buckets:
            buckets[d] = {"passed": 0, "failed": 0}
        if status == "FINISHED":
            buckets[d]["passed"] += cnt
        elif status == "FAILED":
            buckets[d]["failed"] += cnt

    # Fill empty days
    points = []
    cursor = since.date()
    end = datetime.utcnow().date()
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
