"""PlanRun 读侧共享辅助（#1520 timeline / events / 路由壳）。

时间格式化与终态集合原先散落在 ``plan_runs`` 路由与各读侧 service；
本模块收成单一真源。``require_plan_run`` 供多条读端点共用 404 门禁。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy.orm import Session

from backend.models.enums import PlanRunStatus
from backend.models.plan_run import PlanRun

LIVE_PATROL_HEARTBEAT_WINDOW = timedelta(seconds=180)
TERMINAL_PR_STATUSES = {
    PlanRunStatus.SUCCESS.value,
    PlanRunStatus.PARTIAL_SUCCESS.value,
    PlanRunStatus.FAILED.value,
}


def aware(ts: datetime | None) -> datetime | None:
    """Normalise naive datetimes to UTC（SQLite 测试库无 tzinfo）。"""
    if ts is None:
        return None
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts


def iso(v) -> str | None:
    if v is None:
        return None
    return v.isoformat()


def duration_seconds(start, end) -> float | None:
    if start is None:
        return None
    if end is None:
        end = datetime.now(timezone.utc)
    try:
        return max(0.0, (aware(end) - aware(start)).total_seconds())
    except TypeError:
        return None


def min_aware_dt(*values: datetime | None) -> datetime | None:
    present = [aware(v) for v in values if v is not None]
    return min(present) if present else None


def max_aware_dt(*values: datetime | None) -> datetime | None:
    present = [aware(v) for v in values if v is not None]
    return max(present) if present else None


def require_plan_run(db: Session, run_id: int) -> PlanRun:
    """Load PlanRun or raise 404 — shared by plan_runs read endpoints."""
    pr = db.get(PlanRun, run_id)
    if pr is None:
        raise HTTPException(status_code=404, detail="plan run not found")
    return pr
