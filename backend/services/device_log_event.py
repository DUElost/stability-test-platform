"""DeviceLogEvent query helpers for control-plane SAQ chain (ADR-0028)."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Sequence

from sqlalchemy import bindparam, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
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


def mark_events_archived(
    db: Session,
    plan_run_id: int,
    remote_paths: Sequence[str],
) -> int:
    """extract 成功后把 REMOTE 事件标为 ARCHIVED。"""
    paths = [str(p) for p in remote_paths if p]
    if not paths:
        return 0
    now = datetime.now(timezone.utc)
    result = db.execute(
        update(DeviceLogEvent)
        .where(
            DeviceLogEvent.plan_run_id == plan_run_id,
            DeviceLogEvent.state == EventState.REMOTE.value,
            DeviceLogEvent.remote_path.in_(paths),
        )
        .values(state=EventState.ARCHIVED.value, updated_at=now)
    )
    db.commit()
    return int(result.rowcount or 0)


_LINK_SIGNAL_SQL = text(
    """
    UPDATE job_log_signal AS s
       SET device_log_event_id = e.id
      FROM device_log_event AS e
     WHERE s.job_id = e.job_id
       AND s.seq_no = e.signal_seq_no
       AND s.device_log_event_id IS NULL
       AND e.signal_seq_no IS NOT NULL
       AND e.job_id IN :job_ids
    """
).bindparams(bindparam("job_ids", expanding=True))


async def link_signals_to_device_log_events(
    db: AsyncSession,
    job_ids: Sequence[int],
) -> None:
    """Attach job_log_signal.device_log_event_id when DLE landed first (#214)."""
    ids = sorted({int(jid) for jid in job_ids if jid is not None})
    if not ids:
        return
    await db.execute(_LINK_SIGNAL_SQL, {"job_ids": ids})
