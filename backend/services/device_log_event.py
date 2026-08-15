"""DeviceLogEvent query helpers for control-plane SAQ chain (ADR-0028)."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Sequence

from sqlalchemy import bindparam, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from backend.models.device_log_event import DeviceLogEvent
from backend.models.enums import EventState
from backend.models.job import JobInstance
from backend.models.plan_run import PlanRun

logger = logging.getLogger(__name__)


def continuous_event_upload_enabled() -> bool:
    return os.getenv("STP_EVENT_UPLOADER_ENABLED", "0").strip().lower() in ("1", "true", "yes")


# Upload-complete / extractable: CIFS ``remote_path`` is authoritative.
# Include PRUNED — ``STP_EVENT_UPLOADER_PRUNE_LOCAL`` patches REMOTE→PRUNED
# right after copy (#217); extract must still discover those remote_path dirs.
_REMOTE_STATES = (
    EventState.REMOTE.value,
    EventState.ARCHIVED.value,
    EventState.PRUNED.value,
)

# Clock skew / late upload grace around PlanRun window for unassigned attach (#213 B3).
_ASSOCIATE_GRACE = timedelta(minutes=30)


def count_pending_upload_events(db: Session, plan_run_id: int) -> int:
    """plan_run 下尚未完成上送的事件数。

    ADR-0028 方案 A（STP_EVENT_UPLOADER_CONTINUOUS=0，默认）：仅计数
    UPLOAD_PENDING / UPLOADING / UPLOAD_FAILED；LOCAL 是「有意不传」
    （未被 scan xls 引用），不阻塞 merge。
    逃生阀（STP_EVENT_UPLOADER_CONTINUOUS=1）：计数所有非 REMOTE/ARCHIVED/PRUNED。
    """
    import os
    _continuous = os.getenv("STP_EVENT_UPLOADER_CONTINUOUS", "0").strip().lower() in ("1", "true", "yes")
    if _continuous:
        return int(
            db.execute(
                select(func.count(DeviceLogEvent.id)).where(
                    DeviceLogEvent.plan_run_id == plan_run_id,
                    DeviceLogEvent.state.notin_(_REMOTE_STATES),
                )
            ).scalar()
            or 0
        )
    _IN_FLIGHT = {"UPLOAD_PENDING", "UPLOADING", "UPLOAD_FAILED"}
    return int(
        db.execute(
            select(func.count(DeviceLogEvent.id)).where(
                DeviceLogEvent.plan_run_id == plan_run_id,
                DeviceLogEvent.state.in_(_IN_FLIGHT),
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


def associate_unassigned_events_to_plan_run(db: Session, plan_run_id: int) -> int:
    """Attach ``plan_run_id IS NULL`` events to this PlanRun (#213 B3).

    Two paths (OR):
    1. ``job_id`` belongs to a JobInstance of this PlanRun (strong).
    2. ``job_id IS NULL`` and ``serial`` ∈ PlanRun devices and ``detected_at``
       within ``[started_at - grace, (ended_at or now) + grace]``.
       Serial fallback must not steal events whose ``job_id`` belongs to
       another PlanRun (#230 review).

    Does not move NFS paths; ``remote_path`` may stay under
    ``devices/unassigned/{event_id}/`` — extract still copies via that path.
    """
    from backend.services.plan_run_scan_scope import load_plan_run_device_serials

    plan_run = db.get(PlanRun, plan_run_id)
    if plan_run is None:
        return 0

    now = datetime.now(timezone.utc)
    started = plan_run.started_at or now
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    ended = plan_run.ended_at or now
    if ended.tzinfo is None:
        ended = ended.replace(tzinfo=timezone.utc)
    window_start = started - _ASSOCIATE_GRACE
    window_end = ended + _ASSOCIATE_GRACE

    serials = load_plan_run_device_serials(db, plan_run_id)
    job_ids = db.execute(
        select(JobInstance.id).where(JobInstance.plan_run_id == plan_run_id)
    ).scalars().all()
    job_ids = [int(j) for j in job_ids]

    clauses = []
    if job_ids:
        clauses.append(DeviceLogEvent.job_id.in_(job_ids))
    if serials:
        clauses.append(
            DeviceLogEvent.job_id.is_(None)
            & (DeviceLogEvent.serial.in_(serials))
            & (DeviceLogEvent.detected_at >= window_start)
            & (DeviceLogEvent.detected_at <= window_end)
        )
    if not clauses:
        return 0

    result = db.execute(
        update(DeviceLogEvent)
        .where(
            DeviceLogEvent.plan_run_id.is_(None),
            or_(*clauses),
        )
        .values(plan_run_id=plan_run_id, updated_at=now)
    )
    db.commit()
    n = int(result.rowcount or 0)
    if n:
        logger.info(
            "device_log_event_associated plan_run=%d count=%d",
            plan_run_id, n,
        )
    return n


def mark_events_archived(
    db: Session,
    plan_run_id: int,
    remote_paths: Sequence[str],
) -> int:
    """extract 成功后把仍为 REMOTE 的事件标为 ARCHIVED。

    Already-``PRUNED`` rows (local deleted after upload) keep ``PRUNED``;
    their ``remote_path`` remains extractable via ``list_remote_paths_for_extract``.
    """
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
