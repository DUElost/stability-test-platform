"""PlanRun 读侧共享时间/终态辅助（#1520 timeline + events）。

``_aware`` / ``_LIVE_PATROL_HEARTBEAT_WINDOW`` / ``_TERMINAL_PR_STATUSES`` /
``_iso`` 原先散落在 ``plan_runs`` 路由与 ``plan_run_timeline`` 服务各一份；
本模块收成单一真源，避免 timeline↔events 口径漂移。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.models.enums import PlanRunStatus

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
