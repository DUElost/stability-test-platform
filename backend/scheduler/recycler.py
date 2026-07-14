"""
Job Recycler — timeout recovery for JobInstance lifecycle.

Complements the session watchdog and Reconciler by handling:
- PENDING timeout: agent never claimed the job → FAILED + release lease
- RUNNING timeout: job lost contact → UNKNOWN (lease stays ACTIVE; Reconciler finalizes)
- Artifact file pruning: delete physical files referenced by old StepTrace records

Host heartbeat timeout is handled by session_watchdog.py.
Lease expiration is handled by device_lease_reconciler.py (ADR-0019 Phase 4b).

Entry point: ``recycle_once()`` is invoked by APScheduler IntervalTrigger
(see ``app_scheduler.py``).
"""

import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse

from sqlalchemy import DateTime, Integer, case, cast, exists, func, literal, or_, select, update
from sqlalchemy.dialects.postgresql import JSONB as PG_JSONB
from sqlalchemy.orm import aliased

from backend.core.audit import record_audit
from backend.core.database import SessionLocal
from backend.realtime.socketio_server import schedule_emit
from backend.core.metrics import (
    recycler_runs,
    recycler_timeouts,
    recycler_duration,
    device_lease_released,
    task_run_state_changes,
    task_run_total,
)
from backend.models.enums import JobStatus, LeaseType
from backend.models.job import JobInstance, StepTrace
from backend.models.plan_run import PlanRun
from backend.services.lease_manager import release_lease_sync
from backend.services.state_machine import JobStateMachine, InvalidTransitionError

logger = logging.getLogger(__name__)

from backend.core.job_timeout_config import (
    DISPATCHED_TIMEOUT_SECONDS,
    PATROL_STALL_MULTIPLIER,
    PATROL_RUNNING_HEARTBEAT_TIMEOUT_SECONDS,
    RUNNING_HEARTBEAT_TIMEOUT_SECONDS,
    running_heartbeat_timeout_seconds,
)
RECYCLER_BATCH_SIZE = int(os.getenv("RECYCLER_BATCH_SIZE", "200"))
ARTIFACT_RETENTION_DAYS = int(os.getenv("ARTIFACT_RETENTION_DAYS", "30"))

# ADR-0022 D10: patrol-heartbeat stall detection
PATROL_STALL_BATCH_LIMIT = int(os.getenv("PATROL_STALL_BATCH_LIMIT", "100"))



# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_json_loads(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        payload = json.loads(raw)
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _aware_dt(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _pg_json_path(base, *keys: str):
    expr = base
    for key in keys:
        expr = expr.op("->")(key)
    return expr


def _pg_json_text(base, *keys: str):
    expr = base
    for key in keys[:-1]:
        expr = expr.op("->")(key)
    return expr.op("->>")(keys[-1])


def _completed_init_steps_by_job(db, job_ids: list[int]) -> dict[int, tuple[int, datetime]]:
    if not job_ids:
        return {}

    rows = (
        db.query(
            StepTrace.job_id,
            func.count(func.distinct(StepTrace.step_id)),
            func.max(StepTrace.original_ts),
        )
        .filter(
            StepTrace.job_id.in_(job_ids),
            StepTrace.stage == "init",
            StepTrace.event_type == "COMPLETED",
        )
        .group_by(StepTrace.job_id)
        .all()
    )
    return {
        int(job_id): (int(completed_steps or 0), _aware_dt(init_completed_at))
        for job_id, completed_steps, init_completed_at in rows
        if init_completed_at is not None
    }


def _collect_patrol_stall_candidates_py(db, now: datetime) -> list[tuple[JobInstance, int, float]]:
    candidates = (
        db.query(JobInstance)
        .filter(JobInstance.status == JobStatus.RUNNING.value)
        .all()
    )
    init_completion = _completed_init_steps_by_job(db, [job.id for job in candidates])

    stall_list: list[tuple[float, JobInstance, int, float]] = []
    for job in candidates:
        pipeline_def = job.pipeline_def if isinstance(job.pipeline_def, dict) else _safe_json_loads(job.pipeline_def)
        patrol = (pipeline_def.get("lifecycle") or {}).get("patrol")
        if not isinstance(patrol, dict):
            continue
        interval = patrol.get("interval_seconds")
        if not isinstance(interval, int) or interval < 1:
            continue

        anchor = _aware_dt(job.last_patrol_heartbeat_at)
        if anchor is None:
            init_steps = (pipeline_def.get("lifecycle") or {}).get("init")
            init_steps = init_steps if isinstance(init_steps, list) else []
            if not init_steps:
                anchor = _aware_dt(job.started_at)
            else:
                completed = init_completion.get(job.id)
                if completed is None:
                    continue
                completed_steps, init_completed_at = completed
                if completed_steps < len(init_steps):
                    continue
                anchor = init_completed_at

        if anchor is None:
            continue

        threshold = interval * PATROL_STALL_MULTIPLIER
        age = (now - anchor).total_seconds()
        overdue = age - threshold
        if overdue > 0:
            stall_list.append((overdue, job, interval, age))

    stall_list.sort(key=lambda item: item[0], reverse=True)
    return [(job, interval, age) for _overdue, job, interval, age in stall_list[:PATROL_STALL_BATCH_LIMIT]]


def _build_patrol_stall_candidates_stmt(now: datetime):
    init_done = (
        select(
            StepTrace.job_id.label("job_id"),
            func.count(func.distinct(StepTrace.step_id)).label("completed_steps"),
            func.max(StepTrace.original_ts).label("init_completed_at"),
        )
        .where(
            StepTrace.stage == "init",
            StepTrace.event_type == "COMPLETED",
        )
        .group_by(StepTrace.job_id)
        .subquery()
    )

    interval_expr = cast(
        _pg_json_text(JobInstance.pipeline_def, "lifecycle", "patrol", "interval_seconds"),
        Integer,
    )
    init_steps_json = _pg_json_path(JobInstance.pipeline_def, "lifecycle", "init")
    init_step_count = func.jsonb_array_length(
        cast(func.coalesce(init_steps_json, cast(literal("[]"), PG_JSONB)), PG_JSONB)
    )
    anchor_expr = case(
        (JobInstance.last_patrol_heartbeat_at.isnot(None), JobInstance.last_patrol_heartbeat_at),
        (init_step_count == 0, JobInstance.started_at),
        (init_done.c.completed_steps >= init_step_count, init_done.c.init_completed_at),
        else_=None,
    )
    age_expr = func.extract(
        "epoch",
        cast(literal(now.isoformat()), DateTime(timezone=True)) - anchor_expr,
    )
    overdue_expr = age_expr - (interval_expr * PATROL_STALL_MULTIPLIER)

    return (
        select(
            JobInstance,
            interval_expr.label("interval_seconds"),
            age_expr.label("age_seconds"),
            overdue_expr.label("overdue_seconds"),
        )
        .outerjoin(init_done, init_done.c.job_id == JobInstance.id)
        .where(
            JobInstance.status == JobStatus.RUNNING.value,
            interval_expr.isnot(None),
            interval_expr > 0,
            anchor_expr.isnot(None),
            overdue_expr > 0,
        )
        .order_by(overdue_expr.desc(), JobInstance.id.asc())
        .limit(PATROL_STALL_BATCH_LIMIT)
    )


def _collect_patrol_stall_candidates(db, now: datetime) -> list[tuple[JobInstance, int, float]]:
    bind = db.get_bind() if hasattr(db, "get_bind") else None
    dialect = bind.dialect.name if bind is not None else ""
    if dialect == "postgresql":
        rows = db.execute(_build_patrol_stall_candidates_stmt(now)).all()
        return [
            (job, int(interval_seconds), float(age_seconds))
            for job, interval_seconds, age_seconds, _overdue_seconds in rows
        ]
    return _collect_patrol_stall_candidates_py(db, now)


def _mark_pending_timeout(db, job: JobInstance, now: datetime, reason: str) -> bool:
    """PENDING timeout → FAILED + release lease (defensive, normally no-op).

    Only for jobs the Agent never claimed while the host had no RUNNING work.
    Jobs waiting behind an intentional parallel cap must remain PENDING
    (see recycle_once PENDING filter).

    ADR-0019 Phase 4c: release_lease_sync is kept as a safety net (normal
    PENDING path has no lease).
    """
    _terminal = {
        JobStatus.COMPLETED.value, JobStatus.FAILED.value,
        JobStatus.ABORTED.value, JobStatus.UNKNOWN.value,
    }
    if job.status in _terminal:
        return False
    if job.status != JobStatus.PENDING.value:
        return False  # Phase 4c: only PENDING; RUNNING handled by _mark_running_timeout

    old_status = job.status
    updated = db.execute(
        update(JobInstance)
        .execution_options(synchronize_session=False)
        .where(
            JobInstance.id == job.id,
            JobInstance.status == JobStatus.PENDING.value,
        )
        .values(
            status=JobStatus.FAILED.value,
            status_reason=reason,
            ended_at=now,
            updated_at=now,
        )
        .returning(JobInstance.id)
    ).first()
    if updated is None:
        return False
    db.expire(job)
    job = db.get(JobInstance, job.id)
    if job is None:
        return False
    # Phase 6d: release_lease_sync is now a single UPDATE — no projection,
    # no LeaseProjectionError fallback needed.
    release_lease_sync(db, job.device_id, job.id, LeaseType.JOB)

    try:
        from backend.services.aggregator_sync import plan_aggregator_sync
        plan_aggregator_sync(job, db)
    except Exception as e:
        from backend.core.metrics import record_plan_run_aggregation_failed
        record_plan_run_aggregation_failed()
        logger.warning("recycler_aggregation_failed job=%d: %s", job.id, e)
        raise

    record_audit(
        db,
        action="job_terminalized",
        resource_type="job_instance",
        resource_id=job.id,
        details={
            "plan_run_id": job.plan_run_id,
            "from_status": old_status,
            "to_status": JobStatus.FAILED.value,
            "reason": reason,
            "source": "recycler_pending_timeout",
        },
        username="system",
    )

    # Check if PlanRun became terminal after aggregation (B3)
    plan_run_terminal = False
    pr = db.get(PlanRun, job.plan_run_id)
    if pr is not None and pr.status in {
        "SUCCESS", "PARTIAL_SUCCESS", "FAILED", "DEGRADED",
    }:
        plan_run_terminal = True

    task_run_state_changes.labels(from_state=old_status, to_state="FAILED").inc()
    task_run_total.labels(status="failed", task_type="plan").inc()
    device_lease_released.labels(reason="timeout").inc()
    recycler_timeouts.labels(timeout_type="dispatched").inc()

    logger.warning(
        "job_timeout",
        extra={
            "job_id": job.id,
            "plan_run_id": job.plan_run_id,
            "old_status": old_status,
            "reason": reason,
        },
    )

    room = f"plan_run:{job.plan_run_id}"
    schedule_emit("job_status", {
        "type": "JOB_STATUS",
        "payload": {
            "job_id": job.id,
            "plan_run_id": job.plan_run_id,
            "status": "FAILED",
            "reason": reason,
        },
    }, namespace="/dashboard", room=room)

    if plan_run_terminal:
        schedule_emit("plan_run_status", {
            "type": "PLAN_RUN_STATUS",
            "payload": {
                "plan_run_id": job.plan_run_id,
                "status": pr.status,
            },
        }, namespace="/dashboard", room=room)
    return True


def _mark_running_timeout(db, job: JobInstance, now: datetime, reason: str) -> bool:
    """RUNNING timeout → UNKNOWN (ADR-0019 Phase 4c).

    Lease stays ACTIVE — the device remains blocked. Reconciler will
    finalize (UNKNOWN→FAILED + release lease) after the grace period.

    Does NOT call release_lease_sync, PlanRun aggregation, or emit
    FAILED notification.
    """
    if job.status != JobStatus.RUNNING.value:
        return False

    old_status = job.status
    observed_updated_at = job.updated_at
    updated = db.execute(
        update(JobInstance)
        .execution_options(synchronize_session=False)
        .where(
            JobInstance.id == job.id,
            JobInstance.status == JobStatus.RUNNING.value,
            JobInstance.updated_at == observed_updated_at,
        )
        .values(
            status=JobStatus.UNKNOWN.value,
            status_reason=reason,
            ended_at=now,
            updated_at=now,
        )
        .returning(JobInstance.id)
    ).first()
    if updated is None:
        return False

    task_run_state_changes.labels(from_state=old_status, to_state="UNKNOWN").inc()
    recycler_timeouts.labels(timeout_type="running").inc()

    logger.warning(
        "job_timeout_to_unknown",
        extra={
            "job_id": job.id,
            "plan_run_id": job.plan_run_id,
            "old_status": old_status,
            "reason": reason,
        },
    )

    schedule_emit("job_status", {
        "type": "JOB_STATUS",
        "payload": {
            "job_id": job.id,
            "plan_run_id": job.plan_run_id,
            "status": "UNKNOWN",
            "reason": reason,
        },
    }, namespace="/dashboard", room=f"plan_run:{job.plan_run_id}")
    return True


def _mark_patrol_stall(
    db,
    job: JobInstance,
    now: datetime,
    *,
    interval_seconds: int,
    age_seconds: float,
    reason: str,
    require_missing_heartbeat: bool = False,
) -> bool:
    """ADR-0022 D10: RUNNING→UNKNOWN via atomic CAS. Returns True iff the row was flipped.

    WHERE 三联确保:
      - status='RUNNING' 防御「另一路径已改 status」
      - last_patrol_heartbeat_at < cutoff 防御「heartbeat 端点 (已加 status guard)
        在我们决策与提交之间又把 timestamp 推到 fresh」
      - 首个 patrol 周期走 pre-heartbeat 检测时,要求 last_patrol_heartbeat_at 仍为 NULL,
        防御「候选采集后 heartbeat 正好到达」的竞态。

    返回 False 时调用方跳过 audit/socketio/metric — 没有真正的状态变化。

    显式 CAS 而非 ``JobStateMachine.transition``: 状态机已认可 RUNNING→UNKNOWN 合法;
    CAS 把「读 stale heartbeat → 决策 → 写 UPDATE」三步压成单条 SQL,在 DB 行级别消除竞态。
    """
    cutoff = now - timedelta(seconds=interval_seconds * PATROL_STALL_MULTIPLIER)
    stale_guard = (
        JobInstance.last_patrol_heartbeat_at.is_(None)
        if require_missing_heartbeat
        else JobInstance.last_patrol_heartbeat_at < cutoff
    )
    updated = db.execute(
        update(JobInstance)
        .execution_options(synchronize_session=False)
        .where(
            JobInstance.id == job.id,
            JobInstance.status == JobStatus.RUNNING.value,
            stale_guard,
        )
        .values(
            status=JobStatus.UNKNOWN.value,
            status_reason=reason,
            ended_at=now,
            updated_at=now,
        )
        .returning(JobInstance.id)
    ).first()
    if updated is None:
        return False

    record_audit(
        db,
        action="patrol_stall_detected",
        resource_type="job_instance",
        resource_id=job.id,
        details={
            "plan_run_id": job.plan_run_id,
            "device_id": job.device_id,
            "interval_seconds": interval_seconds,
            "age_seconds": int(age_seconds),
            "multiplier": PATROL_STALL_MULTIPLIER,
            "pre_heartbeat": require_missing_heartbeat,
        },
        username="system",
    )
    recycler_timeouts.labels(timeout_type="patrol_stall").inc()
    task_run_state_changes.labels(from_state="RUNNING", to_state="UNKNOWN").inc()
    logger.warning(
        "patrol_stall_detected",
        extra={
            "job_id": job.id,
            "plan_run_id": job.plan_run_id,
            "interval_seconds": interval_seconds,
            "age_seconds": int(age_seconds),
        },
    )
    schedule_emit(
        "job_status",
        {
            "type": "JOB_STATUS",
            "payload": {
                "job_id": job.id,
                "plan_run_id": job.plan_run_id,
                "status": "UNKNOWN",
                "reason": reason,
            },
        },
        namespace="/dashboard",
        room=f"plan_run:{job.plan_run_id}",
    )
    return True


# ---------------------------------------------------------------------------
# Main recycler pass
# ---------------------------------------------------------------------------

_POST_COMPLETION_GRACE_SECONDS = int(os.getenv("POST_COMPLETION_GRACE_SECONDS", "120"))


def _fill_deferred_post_completions(db, now: datetime) -> int:
    """Enqueue post-completion via SAQ for terminal jobs the primary path missed.

    Waits POST_COMPLETION_GRACE_SECONDS after ended_at before triggering,
    giving the agent's outbox drain a window to be the first writer.
    """
    from backend.tasks.saq_worker import enqueue_sync

    grace_deadline = now - timedelta(seconds=_POST_COMPLETION_GRACE_SECONDS)
    terminal_statuses = [
        JobStatus.COMPLETED.value, JobStatus.FAILED.value,
        JobStatus.ABORTED.value,
    ]
    orphan_jobs = (
        db.query(JobInstance)
        .filter(
            JobInstance.status.in_(terminal_statuses),
            JobInstance.post_processed_at.is_(None),
            JobInstance.ended_at.isnot(None),
            JobInstance.ended_at < grace_deadline,
        )
        .limit(10)
        .all()
    )

    filled = 0
    for job in orphan_jobs:
        try:
            enqueue_sync(
                "post_completion_task",
                key=f"pc:{job.id}",
                timeout=120,
                retries=3,
                job_id=job.id,
            )
            filled += 1
            logger.info("deferred_post_completion_enqueued job=%d", job.id)

            event_type = "RUN_FAILED" if job.status == JobStatus.FAILED.value else "RUN_COMPLETED"
            enqueue_sync(
                "send_notification_task",
                key=f"notif:{job.id}:{event_type}",
                event_type=event_type,
                context={
                    "run_id": job.id,
                    "task_id": job.plan_run_id,
                    "task_name": f"job-{job.id}",
                    "task_type": "plan",
                    "error_message": job.status_reason or "",
                    "device_serial": str(job.device_id),
                },
            )
        except Exception:
            logger.exception("deferred_post_completion_enqueue_failed job=%d", job.id)

    return filled


def recycle_once() -> None:
    start_time = time.time()
    now = datetime.now(timezone.utc)
    pending_deadline = now - timedelta(seconds=DISPATCHED_TIMEOUT_SECONDS)

    # 1) PENDING timeout — only jobs whose host has *no* RUNNING work.
    #    Excess PENDING behind Agent parallel capacity must stay queued
    #    (FIFO claim when slots free), not fail as pending_timeout.
    while True:
        with SessionLocal() as db:
            running_job = aliased(JobInstance)
            host_has_running_job = exists(
                select(1).where(
                    running_job.host_id == JobInstance.host_id,
                    running_job.status == JobStatus.RUNNING.value,
                )
            )
            batch = (
                db.query(JobInstance)
                .filter(
                    JobInstance.status == JobStatus.PENDING.value,
                    JobInstance.created_at < pending_deadline,
                    or_(
                        JobInstance.host_id.is_(None),
                        ~host_has_running_job,
                    ),
                )
                .order_by(JobInstance.id)
                .limit(RECYCLER_BATCH_SIZE)
                .all()
            )
            if not batch:
                break
            for job in batch:
                try:
                    with db.begin_nested():
                        _mark_pending_timeout(
                            db, job, now, "pending_timeout: agent never claimed job",
                        )
                except Exception as exc:
                    record_audit(
                        db,
                        action="job_terminalization_failed",
                        resource_type="job_instance",
                        resource_id=job.id,
                        details={
                            "plan_run_id": job.plan_run_id,
                            "source": "recycler_pending_timeout",
                            "error": str(exc)[:500],
                        },
                        username="system",
                    )
                    logger.exception(
                        "recycler_pending_failed job=%d device=%d",
                        job.id, job.device_id,
                    )
            db.commit()

    # 2) RUNNING timeout → UNKNOWN (Phase 4c). Same batched approach.
    #    Uses graded per-job timeout when pipeline has an active patrol phase.
    min_running_timeout = min(
        RUNNING_HEARTBEAT_TIMEOUT_SECONDS,
        PATROL_RUNNING_HEARTBEAT_TIMEOUT_SECONDS,
    )
    running_prefetch_deadline = now - timedelta(seconds=min_running_timeout)
    while True:
        with SessionLocal() as db:
            batch = (
                db.query(JobInstance)
                .filter(
                    JobInstance.status == JobStatus.RUNNING.value,
                    JobInstance.updated_at < running_prefetch_deadline,
                )
                .order_by(JobInstance.id)
                .limit(RECYCLER_BATCH_SIZE)
                .all()
            )
            if not batch:
                break
            for job in batch:
                job_deadline = now - timedelta(
                    seconds=running_heartbeat_timeout_seconds(job)
                )
                updated_at = _aware_dt(job.updated_at)
                if updated_at is None or updated_at >= job_deadline:
                    continue
                try:
                    with db.begin_nested():
                        _mark_running_timeout(
                            db, job, now, "running_timeout: no completion within window",
                        )
                except Exception:
                    logger.exception(
                        "recycler_running_failed job=%d device=%d",
                        job.id, job.device_id,
                    )
            db.commit()

    # 2b) PATROL stall — patrol-heartbeat 长时间未更新视为 patrol 循环卡死。
    #     已有 heartbeat 的 RUNNING patrol job 在 PostgreSQL 上直接由 SQL 侧计算
    #     overdue 并 LIMIT，避免每个 tick 把所有健康 job 和 pipeline_def 全量拉回 Python。
    #     首个 patrol 周期尚未产出 heartbeat 时，若 init 已完成，则改用
    #     started_at / init 完成时间作为首周期锚点，避免只能退回 15 分钟 RUNNING timeout。
    with SessionLocal() as db:
        stall_transitions = 0
        for job, interval, age in _collect_patrol_stall_candidates(db, now):
            reason = (
                f"patrol_stall: age={int(age)}s > "
                f"{interval}*{PATROL_STALL_MULTIPLIER}={interval * PATROL_STALL_MULTIPLIER}s"
            )
            try:
                with db.begin_nested():
                    if _mark_patrol_stall(
                        db, job, now,
                        interval_seconds=interval,
                        age_seconds=age,
                        reason=reason,
                        require_missing_heartbeat=job.last_patrol_heartbeat_at is None,
                    ):
                        stall_transitions += 1
            except Exception:
                logger.exception("recycler_patrol_stall_failed job=%d", job.id)
        if stall_transitions:
            db.commit()

    # 3) Deferred post-completion for orphan terminal jobs
    with SessionLocal() as db:
        filled = _fill_deferred_post_completions(db, now)
        if filled:
            logger.info("deferred_post_completions_filled count=%d", filled)

    # 4) Prune old artifact files
    with SessionLocal() as db:
        _prune_steptrace_artifacts(db, now)

    duration = time.time() - start_time
    recycler_duration.observe(duration)
    recycler_runs.inc()


# ---------------------------------------------------------------------------
# Artifact pruning
# ---------------------------------------------------------------------------

def _prune_steptrace_artifacts(db, now: datetime) -> None:
    """Delete physical artifact files referenced by old StepTrace completion records.

    Only deletes file:// URIs. StepTrace rows are preserved (audit records).
    """
    cutoff = now - timedelta(days=ARTIFACT_RETENTION_DAYS)

    old_traces = (
        db.query(StepTrace)
        .filter(
            StepTrace.step_id == "__job__",
            StepTrace.event_type == "RUN_COMPLETE",
            StepTrace.created_at < cutoff,
        )
        .all()
    )

    if not old_traces:
        return

    file_deleted_count = 0
    for trace in old_traces:
        snapshot = _safe_json_loads(trace.output)
        artifact = snapshot.get("artifact") if isinstance(snapshot, dict) else None
        if not artifact or not isinstance(artifact, dict):
            continue
        storage_uri = artifact.get("storage_uri")
        if not storage_uri:
            continue
        try:
            parsed = urlparse(storage_uri)
            if parsed.scheme.lower() == "file":
                if parsed.netloc and parsed.path:
                    local_path = Path(f"//{parsed.netloc}{unquote(parsed.path)}")
                elif parsed.netloc and not parsed.path:
                    local_path = Path(unquote(parsed.netloc))
                else:
                    local_path = Path(unquote(parsed.path))
                if local_path.exists() and local_path.is_file():
                    os.remove(local_path)
                    file_deleted_count += 1
        except Exception as e:
            logger.warning(f"Failed to delete artifact file {storage_uri}: {e}")

    if file_deleted_count:
        logger.info(
            "steptrace_artifacts_pruned",
            extra={"traces_scanned": len(old_traces), "files_deleted": file_deleted_count},
        )
