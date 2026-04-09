"""Agent API: job claim, status update, step trace upload, heartbeat.

Authentication: X-Agent-Secret header (compared to AGENT_SECRET env var).
"""

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.response import ApiResponse, err, ok
from backend.core.database import get_async_db
from backend.models.enums import HostStatus, JobStatus
from backend.models.host import Device, Host
from backend.models.job import JobInstance, StepTrace
from backend.services.aggregator import WorkflowAggregator
from backend.services.device_lock import acquire_lock, extend_lock, release_lock
from backend.services.reconciler import reconcile_step_traces
from backend.services.state_machine import InvalidTransitionError, JobStateMachine

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/agent", tags=["agent"])

_AGENT_SECRET = os.getenv("AGENT_SECRET", "")
_DEVICE_LOCK_LEASE_SECONDS = int(os.getenv("DEVICE_LOCK_LEASE_SECONDS", "600"))
_TERMINAL = {
    JobStatus.COMPLETED.value,
    JobStatus.FAILED.value,
    JobStatus.ABORTED.value,
    JobStatus.UNKNOWN.value,
}


class _LockAcquireFailed(Exception):
    """Raised inside a savepoint when device lock acquire fails."""
    pass


def _verify_agent(x_agent_secret: Optional[str] = Header(None, alias="X-Agent-Secret")):
    if _AGENT_SECRET and x_agent_secret != _AGENT_SECRET:
        raise HTTPException(status_code=401, detail="invalid agent secret")


# ── Schemas ───────────────────────────────────────────────────────────────────

class ClaimRequest(BaseModel):
    host_id: str
    capacity: int = 10


class JobOut(BaseModel):
    id: int
    workflow_run_id: int
    task_template_id: int
    device_id: int
    device_serial: Optional[str] = None
    host_id: Optional[str]
    status: str
    pipeline_def: dict


class JobStatusUpdate(BaseModel):
    status: str
    reason: str = ""


class StepTraceIn(BaseModel):
    job_id: int
    step_id: str
    stage: str = "execute"
    event_type: str
    status: str = ""
    output: Optional[str] = None
    error_message: Optional[str] = None
    original_ts: Optional[str] = None


class HeartbeatRequest(BaseModel):
    host_id: str
    tool_catalog_version: str = ""
    load: Dict[str, Any] = {}


class BackpressureInfo(BaseModel):
    log_rate_limit: Optional[int]


class HeartbeatResponse(BaseModel):
    tool_catalog_outdated: bool
    backpressure: BackpressureInfo


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/jobs/claim", response_model=ApiResponse[List[JobOut]])
async def claim_jobs(
    payload: ClaimRequest,
    db: AsyncSession = Depends(get_async_db),
    _=Depends(_verify_agent),
):
    """Find PENDING jobs for devices on this host, claim up to `capacity`.

    Per-device deduplication: only one job per device is claimed per call.
    Device lock guard: skips jobs for devices locked by a different job.
    """
    device_ids_result = await db.execute(
        select(Device.id).where(Device.host_id == payload.host_id)
    )
    device_ids = [row[0] for row in device_ids_result.all()]
    if not device_ids:
        return ok([])

    pending_jobs = (await db.execute(
        select(JobInstance)
        .where(
            JobInstance.device_id.in_(device_ids),
            JobInstance.status == JobStatus.PENDING.value,
        )
        .order_by(JobInstance.created_at)
        .limit(payload.capacity)
    )).scalars().all()

    # Pre-fetch device lock state
    lock_map: Dict[int, Optional[int]] = {}
    if pending_jobs:
        lock_rows = await db.execute(
            select(Device.id, Device.lock_run_id, Device.lock_expires_at)
            .where(Device.id.in_([j.device_id for j in pending_jobs]))
        )
        now_utc = datetime.now(timezone.utc)
        for row in lock_rows.all():
            if row.lock_run_id and row.lock_expires_at and row.lock_expires_at >= now_utc:
                lock_map[row.id] = row.lock_run_id
            else:
                lock_map[row.id] = None

    claimed = []
    claimed_device_ids: set[int] = set()
    now = datetime.now(timezone.utc)
    for job in pending_jobs:
        if job.device_id in claimed_device_ids:
            continue

        holder = lock_map.get(job.device_id)
        if holder is not None and holder != job.id:
            continue

        try:
            async with db.begin_nested():
                JobStateMachine.transition(job, JobStatus.RUNNING, "claimed_by_agent")
                job.host_id = payload.host_id
                job.started_at = now

                acquired = await acquire_lock(db, job.device_id, job.id, _DEVICE_LOCK_LEASE_SECONDS)
                if not acquired:
                    raise _LockAcquireFailed()

            claimed.append(job)
            claimed_device_ids.add(job.device_id)
        except _LockAcquireFailed:
            # Savepoint already rolled back by begin_nested() context manager
            continue
        except InvalidTransitionError:
            continue

    if claimed:
        await db.commit()
    return ok([
        JobOut(
            id=j.id, workflow_run_id=j.workflow_run_id,
            task_template_id=j.task_template_id, device_id=j.device_id,
            host_id=j.host_id, status=j.status, pipeline_def=j.pipeline_def,
        )
        for j in claimed
    ])


@router.post("/jobs/{job_id}/status", response_model=ApiResponse[dict])
async def update_job_status(
    job_id: int,
    payload: JobStatusUpdate,
    db: AsyncSession = Depends(get_async_db),
    _=Depends(_verify_agent),
):
    """Transition job status via JobStateMachine."""
    job = await db.get(JobInstance, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")

    try:
        new_status = JobStatus(payload.status.upper())
    except ValueError:
        raise HTTPException(status_code=400, detail=f"unknown status: {payload.status}")

    try:
        JobStateMachine.transition(job, new_status, payload.reason)
    except InvalidTransitionError as e:
        raise HTTPException(status_code=409, detail=str(e))

    if job.status in _TERMINAL:
        job.ended_at = datetime.utcnow()
        await WorkflowAggregator.on_job_terminal(job, db)

    await db.commit()
    return ok({"job_id": job_id, "status": job.status})


@router.post("/steps", response_model=ApiResponse[dict])
async def upload_step_traces(
    traces: List[StepTraceIn],
    db: AsyncSession = Depends(get_async_db),
    _=Depends(_verify_agent),
    x_agent_secret: Optional[str] = Header(None, alias="X-Agent-Secret"),
):
    """Batch idempotent StepTrace upsert (Agent replay on reconnect)."""
    host_id = "unknown"
    raw = [t.model_dump() for t in traces]
    inserted = await reconcile_step_traces(host_id, raw, db)
    return ok({"inserted": inserted, "total": len(traces)})


@router.post("/heartbeat", response_model=ApiResponse[HeartbeatResponse])
async def agent_heartbeat(
    payload: HeartbeatRequest,
    db: AsyncSession = Depends(get_async_db),
    _=Depends(_verify_agent),
):
    """
    Update host last_heartbeat + tool_catalog_version.
    Returns tool_catalog_outdated flag + current backpressure setting.
    """
    host = await db.get(Host, payload.host_id)
    if host is None:
        host = Host(
            id=payload.host_id,
            hostname=payload.host_id,
            status=HostStatus.ONLINE.value,
            created_at=datetime.utcnow(),
        )
        db.add(host)

    outdated = (
        bool(host.tool_catalog_version)
        and host.tool_catalog_version != payload.tool_catalog_version
    )

    host.last_heartbeat = datetime.utcnow()
    host.tool_catalog_version = payload.tool_catalog_version
    host.status = HostStatus.ONLINE.value

    await db.commit()

    backpressure = await _get_backpressure()
    return ok(HeartbeatResponse(
        tool_catalog_outdated=outdated,
        backpressure=BackpressureInfo(log_rate_limit=backpressure),
    ))


# ── RunStatus→JobStatus mapping for compat endpoints ─────────────────────────

_RUN_TO_JOB: Dict[str, JobStatus] = {
    "RUNNING":   JobStatus.RUNNING,
    "FINISHED":  JobStatus.COMPLETED,
    "COMPLETED": JobStatus.COMPLETED,
    "FAILED":    JobStatus.FAILED,
    "CANCELED":  JobStatus.ABORTED,
    "CANCELLED": JobStatus.ABORTED,
    "ABORTED":   JobStatus.ABORTED,
}


class _JobHeartbeatIn(BaseModel):
    status: str = "RUNNING"
    started_at: Optional[str] = None


class _RunCompleteIn(BaseModel):
    update: Dict[str, Any]
    artifact: Optional[Dict[str, Any]] = None


class _StepStatusIn(BaseModel):
    status: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    exit_code: Optional[int] = None
    error_message: Optional[str] = None


# ── Compat endpoints (mirroring old /runs/* interface under /jobs/*) ──────────


@router.get("/jobs/pending", response_model=ApiResponse[List[JobOut]])
async def get_pending_jobs(
    host_id: str,
    limit: int = 10,
    db: AsyncSession = Depends(get_async_db),
    _=Depends(_verify_agent),
):
    """Return PENDING JobInstances for devices on this host (pull model).

    Atomically transitions matched jobs to RUNNING to prevent duplicate claims.
    Per-device deduplication: only the earliest PENDING job per device is claimed.
    Device lock guard: skips jobs for devices locked by a different running job.
    """
    device_ids_result = await db.execute(
        select(Device.id).where(Device.host_id == host_id)
    )
    device_ids = [row[0] for row in device_ids_result.all()]

    if not device_ids:
        logger.info(
            "agent_pending_no_devices: host_id=%s has no registered devices", host_id
        )
        return ok([])

    # Match by device_id (original) OR by pre-assigned host_id (new dispatcher logic)
    from sqlalchemy import or_
    jobs = (await db.execute(
        select(JobInstance)
        .where(
            or_(
                JobInstance.device_id.in_(device_ids),
                JobInstance.host_id == host_id,
            ),
            JobInstance.status == JobStatus.PENDING.value,
        )
        .order_by(JobInstance.created_at)
        .limit(limit)
    )).scalars().all()

    serial_map: Dict[int, str] = {}
    if jobs:
        rows = await db.execute(
            select(Device.id, Device.serial)
            .where(Device.id.in_([j.device_id for j in jobs]))
        )
        serial_map = {row.id: row.serial for row in rows.all()}

    # Pre-fetch device lock state for lock guard
    lock_map: Dict[int, Optional[int]] = {}
    if jobs:
        lock_rows = await db.execute(
            select(Device.id, Device.lock_run_id, Device.lock_expires_at)
            .where(Device.id.in_([j.device_id for j in jobs]))
        )
        now_utc = datetime.now(timezone.utc)
        for row in lock_rows.all():
            # Treat expired locks as free
            if row.lock_run_id and row.lock_expires_at and row.lock_expires_at >= now_utc:
                lock_map[row.id] = row.lock_run_id
            else:
                lock_map[row.id] = None

    # Atomically claim jobs: transition PENDING → RUNNING
    # Per-device deduplication: only one job per device per poll
    claimed = []
    claimed_device_ids: set[int] = set()
    now = datetime.now(timezone.utc)
    for j in jobs:
        # Skip if we already claimed a job for this device this cycle
        if j.device_id in claimed_device_ids:
            continue

        # Skip if device is locked by a different running job
        holder = lock_map.get(j.device_id)
        if holder is not None and holder != j.id:
            logger.debug(
                "claim_skip_locked: job=%d device=%d locked_by=%d", j.id, j.device_id, holder,
            )
            continue

        # Wrap transition + lock acquire in a savepoint
        try:
            async with db.begin_nested():
                JobStateMachine.transition(j, JobStatus.RUNNING, "claimed_by_agent")
                j.host_id = host_id
                j.started_at = now

                acquired = await acquire_lock(db, j.device_id, j.id, _DEVICE_LOCK_LEASE_SECONDS)
                if not acquired:
                    raise _LockAcquireFailed()

            claimed.append(j)
            claimed_device_ids.add(j.device_id)
        except _LockAcquireFailed:
            # Savepoint already rolled back by begin_nested() context manager
            logger.debug("claim_lock_race: job=%d device=%d", j.id, j.device_id)
            continue
        except InvalidTransitionError:
            continue

    if claimed:
        await db.commit()
        logger.info(
            "agent_claimed_jobs: host_id=%s, claimed=%d, device_ids=%s",
            host_id, len(claimed), [j.device_id for j in claimed],
        )

    return ok([
        JobOut(
            id=j.id, workflow_run_id=j.workflow_run_id,
            task_template_id=j.task_template_id, device_id=j.device_id,
            device_serial=serial_map.get(j.device_id),
            host_id=j.host_id, status=j.status, pipeline_def=j.pipeline_def,
        )
        for j in claimed
    ])


@router.post("/jobs/{job_id}/heartbeat", response_model=ApiResponse[dict])
async def job_heartbeat(
    job_id: int,
    payload: _JobHeartbeatIn,
    db: AsyncSession = Depends(get_async_db),
    _=Depends(_verify_agent),
):
    """Keep job alive / transition to RUNNING."""
    job = await db.get(JobInstance, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")

    target = _RUN_TO_JOB.get(payload.status.upper(), JobStatus.RUNNING)
    try:
        JobStateMachine.transition(job, target, "agent_heartbeat")
        if target == JobStatus.RUNNING and not job.started_at:
            job.started_at = datetime.now(timezone.utc)
    except InvalidTransitionError:
        pass  # idempotent — already in target state

    await db.commit()
    return ok({"job_id": job_id, "status": job.status})


@router.post("/jobs/{job_id}/complete", response_model=ApiResponse[dict])
async def complete_job(
    job_id: int,
    payload: _RunCompleteIn,
    db: AsyncSession = Depends(get_async_db),
    _=Depends(_verify_agent),
):
    """Transition job to a terminal status."""
    job = await db.get(JobInstance, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")

    raw = str(payload.update.get("status", "FAILED")).upper()
    target = _RUN_TO_JOB.get(raw, JobStatus.FAILED)

    # Idempotent: if MQ consumer already transitioned to the same terminal state,
    # skip the transition but still persist snapshot and release lock.
    already_terminal = job.status == target.value and job.status in _TERMINAL
    if not already_terminal:
        try:
            JobStateMachine.transition(job, target, payload.update.get("error_message") or "")
        except InvalidTransitionError as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": str(exc),
                    "current_status": job.status,
                    "requested_status": target.value,
                },
            )

    # 持久化一次性完成快照（log_summary + artifact），为新链路报告读取提供数据闭环。
    snapshot = {
        "update": payload.update,
        "artifact": payload.artifact,
    }
    snapshot_output = json.dumps(snapshot, ensure_ascii=False)
    now_ts = datetime.now(timezone.utc)
    existing_snapshot = (
        await db.execute(
            select(StepTrace).where(
                StepTrace.job_id == job_id,
                StepTrace.step_id == "__job__",
                StepTrace.event_type == "RUN_COMPLETE",
            )
        )
    ).scalars().first()
    if existing_snapshot:
        existing_snapshot.stage = "post_process"
        existing_snapshot.status = target.value
        existing_snapshot.output = snapshot_output
        existing_snapshot.error_message = payload.update.get("error_message")
        existing_snapshot.original_ts = now_ts
    else:
        db.add(
            StepTrace(
                job_id=job_id,
                step_id="__job__",
                stage="post_process",
                status=target.value,
                event_type="RUN_COMPLETE",
                output=snapshot_output,
                error_message=payload.update.get("error_message"),
                original_ts=now_ts,
                created_at=datetime.utcnow(),
            )
        )

    job.ended_at = datetime.now(timezone.utc)
    if job.status in _TERMINAL:
        await WorkflowAggregator.on_job_terminal(job, db)
        # Release device lock on terminal state
        await release_lock(db, job.device_id, job_id)

    await db.commit()

    if job.status in _TERMINAL:
        try:
            from backend.tasks.saq_worker import get_queue
            from saq import Job as SaqJob

            await get_queue().enqueue(
                SaqJob(
                    function="post_completion_task",
                    kwargs={"job_id": job_id},
                    key=f"pc:{job_id}",
                    timeout=120,
                    retries=3,
                    retry_delay=5.0,
                    retry_backoff=True,
                )
            )
        except Exception as e:
            logger.warning("post_completion enqueue failed for job %d: %s", job_id, e)

    return ok({"job_id": job_id, "status": job.status})


@router.post("/jobs/{job_id}/extend_lock", response_model=ApiResponse[dict])
async def extend_job_lock(
    job_id: int,
    db: AsyncSession = Depends(get_async_db),
    _=Depends(_verify_agent),
):
    """Extend device lock lease for a running job."""
    job = await db.get(JobInstance, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")

    device = await db.get(Device, job.device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="device not found")

    extended = await extend_lock(db, job.device_id, job_id, _DEVICE_LOCK_LEASE_SECONDS)
    if not extended:
        raise HTTPException(status_code=409, detail="device locked by another job")

    await db.commit()
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=_DEVICE_LOCK_LEASE_SECONDS)
    return ok({"job_id": job_id, "expires_at": expires_at.isoformat()})


@router.post("/jobs/{job_id}/steps/{step_id}/status", response_model=ApiResponse[dict])
async def update_job_step_status(
    job_id: int,
    step_id: str,
    payload: _StepStatusIn,
    db: AsyncSession = Depends(get_async_db),
    _=Depends(_verify_agent),
):
    """Update a single step status — upserted as StepTrace."""
    from backend.services.reconciler import reconcile_step_traces
    trace = {
        "job_id": job_id,
        "step_id": step_id,
        "stage": "execute",
        "event_type": "status_update",
        "status": payload.status,
        "error_message": payload.error_message,
        "original_ts": payload.started_at or datetime.now(timezone.utc).isoformat(),
    }
    await reconcile_step_traces("agent", [trace], db)
    return ok({"job_id": job_id, "step_id": step_id, "status": payload.status})


async def _get_backpressure() -> Optional[int]:
    """Return current backpressure setting.

    Redis-based backpressure (stp:backpressure:*) removed in Phase 4.
    SocketIO has built-in TCP backpressure; this returns None (no limit).
    Can be extended later with SocketIO-based metrics if needed.
    """
    return None
