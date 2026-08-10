"""DeviceLogEvent query helpers for control-plane SAQ chain (ADR-0028)."""

from __future__ import annotations

import os
from datetime import datetime
from typing import Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.models.device_log_event import DeviceLogEvent
from backend.models.enums import EventState


def continuous_event_upload_enabled() -> bool:
    return os.getenv("STP_EVENT_UPLOADER_ENABLED", "0").strip().lower() in ("1", "true", "yes")


_REMOTE_STATES = (EventState.REMOTE.value, EventState.ARCHIVED.value)


def count_pending_upload_events(db: Session, plan_run_id: int) -> int:
    """plan_run 下尚未到达 REMOTE/ARCHIVED 的事件数。"""
    return int(
        db.execute(
            select(func.count(DeviceLogEvent.id)).where(
                DeviceLogEvent.plan_run_id == plan_run_id,
                DeviceLogEvent.state.notin_(_REMOTE_STATES),
            )
        ).scalar()
        or 0
    )


def count_remote_events(
    db: Session,
    plan_run_id: int,
    *,
    host_ids: Sequence[str] | None = None,
    since: datetime | None = None,
) -> int:
    stmt = select(func.count(func.distinct(DeviceLogEvent.host_id))).where(
        DeviceLogEvent.plan_run_id == plan_run_id,
        DeviceLogEvent.state.in_(_REMOTE_STATES),
    )
    if host_ids:
        stmt = stmt.where(DeviceLogEvent.host_id.in_(list(host_ids)))
    if since is not None:
        stmt = stmt.where(DeviceLogEvent.updated_at >= since)
    return int(db.execute(stmt).scalar() or 0)


def list_remote_paths_for_extract(db: Session, plan_run_id: int) -> list[str]:
    rows = db.execute(
        select(DeviceLogEvent.remote_path).where(
            DeviceLogEvent.plan_run_id == plan_run_id,
            DeviceLogEvent.state.in_(_REMOTE_STATES),
            DeviceLogEvent.remote_path.isnot(None),
        )
    ).scalars().all()
    return [str(p) for p in rows if p]
