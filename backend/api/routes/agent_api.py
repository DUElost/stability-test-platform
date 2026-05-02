"""Agent API: job claim, status update, step trace upload, heartbeat.

Authentication: X-Agent-Secret header (compared to AGENT_SECRET env var).
"""

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.response import ApiResponse, err, ok
from backend.core.database import get_async_db
from backend.models.enums import HostStatus, JobStatus, LeaseStatus, LeaseType
from backend.models.host import Device, Host
from backend.models.device_lease import DeviceLease
from backend.models.job import JobArtifact, JobInstance, JobLogSignal, StepTrace, TaskTemplate
from backend.models.workflow import WorkflowDefinition
from backend.services.aggregator import WorkflowAggregator
from backend.services.lease_manager import acquire_lease, extend_lease, release_lease
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


async def _enrich_job_metadata(
    db: AsyncSession, jobs: List[JobInstance],
) -> tuple[Dict[int, str], Dict[int, Optional[Dict[str, Any]]]]:
    """批量获取 JobOut 需要的 device_serial + watcher_policy。

    返回：
        serial_map:         device_id -> serial
        watcher_policy_map: job_id    -> watcher_policy (来自 WorkflowDefinition.watcher_policy)
    空 jobs 返回 ({}, {})，避免空集合上的 IN ()（PostgreSQL 会报语法错误）。
    """
    if not jobs:
        return {}, {}

    device_ids = [j.device_id for j in jobs]
    serial_rows = await db.execute(
        select(Device.id, Device.serial).where(Device.id.in_(device_ids))
    )
    serial_map = {row.id: row.serial for row in serial_rows.all()}

    template_ids = {j.task_template_id for j in jobs}
    # task_template → workflow_definition → watcher_policy
    policy_rows = await db.execute(
        select(TaskTemplate.id, WorkflowDefinition.watcher_policy)
        .join(WorkflowDefinition, WorkflowDefinition.id == TaskTemplate.workflow_definition_id)
        .where(TaskTemplate.id.in_(template_ids))
    )
    template_policy = {row.id: row.watcher_policy for row in policy_rows.all()}
    watcher_policy_map = {j.id: template_policy.get(j.task_template_id) for j in jobs}

    return serial_map, watcher_policy_map


# ── Schemas ───────────────────────────────────────────────────────────────────

class ClaimRequest(BaseModel):
    host_id: str
    capacity: int = 10
    agent_instance_id: str = ""   # ADR-0019 Phase 3a


class JobOut(BaseModel):
    id: int
    workflow_run_id: int
    task_template_id: int
    device_id: int
    device_serial: Optional[str] = None
    host_id: Optional[str]
    status: str
    pipeline_def: dict
    # Watcher 策略覆盖（来自 WorkflowDefinition.watcher_policy）
    # Agent 解析见 backend/agent/watcher/policy.py WatcherPolicy.from_job
    watcher_policy: Optional[Dict[str, Any]] = None
    fencing_token: str  # ADR-0019 Phase 2b: 必填，来自 DeviceLease.fencing_token


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
    script_catalog_version: str = ""
    load: Dict[str, Any] = {}
    capacity: Optional[Dict[str, Any]] = None  # ADR-0019 Phase 1
    agent_instance_id: str = ""   # ADR-0019 Phase 3a
    boot_id: str = ""             # ADR-0019 Phase 3a


class BackpressureInfo(BaseModel):
    log_rate_limit: Optional[int]


class HeartbeatResponse(BaseModel):
    tool_catalog_outdated: bool
    script_catalog_outdated: bool = False
    backpressure: BackpressureInfo
    capacity: Optional[Dict[str, Any]] = None  # ADR-0019 Phase 1


# ── ADR-0019 Phase 3a: Recovery Sync models ──────────────────────────────────


class _ActiveJobEntry(BaseModel):
    job_id: int
    device_id: int
    fencing_token: str = ""


class _OutboxEntry(BaseModel):
    job_id: int
    event_type: str = "RUN_COMPLETED"


class _RecoverySyncIn(BaseModel):
    host_id: str
    agent_instance_id: str = ""
    boot_id: str = ""
    active_jobs: List[_ActiveJobEntry] = []
    pending_outbox: List[_OutboxEntry] = []


class _RecoveryAction(BaseModel):
    job_id: int
    device_id: Optional[int] = None
    action: str       # RESUME | CLEANUP | ABORT_LOCAL | UPLOAD_TERMINAL | NOOP
    fencing_token: str = ""
    event_type: str = ""
    reason: str = ""


class _RecoverySyncOut(BaseModel):
    data: dict  # {"actions": [...], "outbox_actions": [...]}


# ── Shared helpers ────────────────────────────────────────────────────────────


async def _claim_jobs_for_host(
    db: AsyncSession,
    host_id: str,
    capacity: int = 10,
    agent_instance_id: str = "",
) -> tuple[List[JobInstance], Dict[int, str]]:
    """Shared claim logic for claim_jobs (POST) and get_pending_jobs (GET).

    Phase 2d hardening:
    - Host row FOR UPDATE serializes concurrent claims for the same host
    - Capacity is authoritative (host.max_concurrent_jobs), Agent's "capacity" is a soft cap
    - Non-expired ACTIVE leases (JOB/Script/MAINTENANCE) pre-filter busy devices
    - row_number() per device picks the earliest PENDING job
    - FOR UPDATE OF JobInstance SKIP LOCKED prevents thundering herd
    - Unified exit: commit on success, rollback on empty to release host lock

    Returns (claimed_jobs, fencing_token_map).
    """
    now = datetime.now(timezone.utc)

    # 1. Lock host row — serializes concurrent claims for the same host
    host_row = (await db.execute(
        select(Host).where(Host.id == host_id).with_for_update()
    )).scalars().first()
    if not host_row:
        return [], {}  # host not found, no lock acquired — safe early return

    # 2. Count ALL ACTIVE JOB leases — Phase 4b blocking lease:
    #    expired (grace-held) leases still occupy capacity slots.
    active_job_count = (await db.execute(
        select(func.count()).select_from(DeviceLease).where(
            DeviceLease.host_id == host_id,
            DeviceLease.lease_type == LeaseType.JOB.value,
            DeviceLease.status == LeaseStatus.ACTIVE.value,
        )
    )).scalar_one()

    # 3. Effective capacity — Agent's "capacity" is only a soft cap
    effective_capacity = max(0, host_row.max_concurrent_jobs - active_job_count)
    effective_capacity = min(effective_capacity, capacity)

    # 4. Get all device IDs for this host (Phase 3c: filter known-unhealthy devices)
    #    - INCLUDE: known-healthy OR never-reported (NULL adb fields → coalesce to safe default)
    #    - EXCLUDE: known-offline (adb_connected=False, bad adb_state, status=OFFLINE)
    device_ids_result = await db.execute(
        select(Device.id).where(
            Device.host_id == host_id,
            func.coalesce(Device.adb_connected, True) == True,
            func.coalesce(Device.adb_state, "device").notin_(["offline", "unknown"]),
            Device.status != "OFFLINE",
        )
    )
    all_device_ids = [row[0] for row in device_ids_result.all()]

    # 5. Pre-filter: exclude devices with ANY ACTIVE lease (Phase 4b blocking lease).
    #    Expired (grace-held) ACTIVE leases also block the device —
    #    only Reconciler can release them.
    free_device_ids: list[int] = []
    if all_device_ids:
        busy_rows = await db.execute(
            select(DeviceLease.device_id).where(
                DeviceLease.device_id.in_(all_device_ids),
                DeviceLease.status == LeaseStatus.ACTIVE.value,
            )
        )
        busy_device_ids = {row[0] for row in busy_rows.all()}
        free_device_ids = [did for did in all_device_ids if did not in busy_device_ids]

    # 6. Per-device first PENDING job + FOR UPDATE SKIP LOCKED
    pending_jobs: list[JobInstance] = []
    claimed: list[JobInstance] = []
    claimed_device_ids: set[int] = set()
    fencing_token_map: Dict[int, str] = {}

    if effective_capacity > 0 and free_device_ids:
        rn = func.row_number().over(
            partition_by=JobInstance.device_id,
            order_by=(JobInstance.created_at, JobInstance.id),
        ).label("rn")

        ranked = (
            select(JobInstance.id, rn)
            .where(
                JobInstance.device_id.in_(free_device_ids),
                JobInstance.status == JobStatus.PENDING.value,
            )
        ).subquery("ranked")

        pending_jobs = (await db.execute(
            select(JobInstance)
            .join(ranked, JobInstance.id == ranked.c.id)
            .where(ranked.c.rn == 1)
            .order_by(JobInstance.created_at)
            .limit(effective_capacity)
            .with_for_update(of=JobInstance.__table__, skip_locked=True)
        )).scalars().all()

    # 7-8. Claim loop
    for job in pending_jobs:
        if job.device_id in claimed_device_ids:
            continue

        try:
            async with db.begin_nested():
                JobStateMachine.transition(job, JobStatus.RUNNING, "claimed_by_agent")
                job.host_id = host_id
                job.started_at = now

                lease = await acquire_lease(
                    db,
                    device_id=job.device_id,
                    host_id=host_id,
                    lease_type=LeaseType.JOB,
                    agent_instance_id=agent_instance_id,
                    job_id=job.id,
                )
                if lease is None:
                    raise _LockAcquireFailed()
                fencing_token_map[job.id] = lease.fencing_token

            claimed.append(job)
            claimed_device_ids.add(job.device_id)
        except _LockAcquireFailed:
            continue
        except InvalidTransitionError:
            continue

    # 9. Unified exit: commit to release host FOR UPDATE lock
    if claimed:
        await db.commit()
    else:
        await db.rollback()

    return claimed, fencing_token_map


# ── ADR-0019 Phase 4b: Runtime Lease Validation ────────────────────────────────


async def _get_valid_runtime_lease(
    db: AsyncSession,
    job: JobInstance,
    fencing_token: str,
) -> Optional[DeviceLease]:
    """Validate runtime lease for token-gated operations (Phase 4b).

    Returns the valid ACTIVE lease, or None if any check fails.
    Checks (all must pass):
      1. ACTIVE lease exists for (device_id, job_id, JOB)
      2. fencing_token matches
      3. expires_at > now (B: expired lease rejects all runtime ops)
      4. job.status == RUNNING (C: whitelist, not blacklist)
    """
    now = datetime.now(timezone.utc)
    lease = (await db.execute(
        select(DeviceLease).where(
            DeviceLease.device_id == job.device_id,
            DeviceLease.job_id == job.id,
            DeviceLease.lease_type == LeaseType.JOB.value,
            DeviceLease.status == LeaseStatus.ACTIVE.value,
        )
    )).scalars().first()

    if lease is None:
        return None
    if lease.fencing_token != fencing_token:
        return None
    # B: expired lease rejects all runtime operations
    if lease.expires_at is None or lease.expires_at <= now:
        return None
    # C: only RUNNING jobs may perform runtime operations
    if job.status != JobStatus.RUNNING.value:
        return None
    return lease


async def _resume_expired_lease_for_recovery(
    db: AsyncSession,
    lease: DeviceLease,
    job: JobInstance,
    agent_instance_id: str,
    now: datetime,
    grace_seconds: int = 300,
) -> bool:
    """Refresh an expired ACTIVE lease for UNKNOWN→RUNNING recovery (Phase 4b).

    MUST be called under row lock on both lease and job rows.
    Only succeeds when:
      - lease.status == ACTIVE (row-locked, may have been released concurrently)
      - job.status == UNKNOWN (row-locked, may have been finalized concurrently)
      - job.ended_at is within grace period (now - ended_at < grace_seconds)

    Does NOT use extend_lease() — this is the ONLY place that refreshes
    an expired lease, and only under the validated recovery preconditions.
    """
    from backend.models.host import Device

    # Re-check under row lock
    if lease.status != LeaseStatus.ACTIVE.value:
        return False
    if job.status != JobStatus.UNKNOWN.value:
        return False
    if job.ended_at is None:
        return False
    grace_deadline = now - timedelta(seconds=grace_seconds)
    if job.ended_at <= grace_deadline:
        return False

    # Refresh lease TTL — Phase 6d: device_leases is sole source of truth,
    # no projection writes to device.lock_run_id / lock_expires_at.
    new_expires_at = now + timedelta(seconds=_DEVICE_LOCK_LEASE_SECONDS)
    lease.renewed_at = now
    lease.expires_at = new_expires_at
    lease.agent_instance_id = agent_instance_id

    return True


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/jobs/claim", response_model=ApiResponse[List[JobOut]])
async def claim_jobs(
    payload: ClaimRequest,
    db: AsyncSession = Depends(get_async_db),
    _=Depends(_verify_agent),
):
    """Find PENDING jobs for devices on this host, claim up to `capacity`.

    Per-device deduplication: only one job per device is claimed per call.
    Uses device_leases as the sole conflict source (Phase 2c).
    Response includes device_serial + watcher_policy for Agent JobSession boot.
    """
    claimed, fencing_token_map = await _claim_jobs_for_host(
        db, payload.host_id, payload.capacity, payload.agent_instance_id,
    )

    if not claimed:
        return ok([])

    # Enrich response: device_serial + watcher_policy(from WorkflowDefinition)
    serial_map, watcher_policy_map = await _enrich_job_metadata(db, claimed)

    return ok([
        JobOut(
            id=j.id, workflow_run_id=j.workflow_run_id,
            task_template_id=j.task_template_id, device_id=j.device_id,
            device_serial=serial_map.get(j.device_id),
            host_id=j.host_id, status=j.status, pipeline_def=j.pipeline_def,
            watcher_policy=watcher_policy_map.get(j.id),
            fencing_token=fencing_token_map[j.id],
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
    # ADR-0019 Phase 1: capacity safe-read
    capacity = payload.capacity or {}
    max_jobs_from_capacity = capacity.get("max_concurrent_jobs")
    if host is None:
        host = Host(
            id=payload.host_id,
            hostname=payload.host_id,
            status=HostStatus.ONLINE.value,
            created_at=datetime.utcnow(),
            max_concurrent_jobs=(
                max_jobs_from_capacity
                if isinstance(max_jobs_from_capacity, int) and max_jobs_from_capacity > 0
                else 2
            ),
        )
        db.add(host)
    elif isinstance(max_jobs_from_capacity, int) and max_jobs_from_capacity > 0:
        host.max_concurrent_jobs = max_jobs_from_capacity

    outdated = (
        bool(payload.tool_catalog_version)
        and bool(host.tool_catalog_version)
        and host.tool_catalog_version != payload.tool_catalog_version
    )
    scripts_outdated = (
        bool(payload.script_catalog_version)
        and bool(host.script_catalog_version)
        and host.script_catalog_version != payload.script_catalog_version
    )

    host.last_heartbeat = datetime.utcnow()
    if payload.tool_catalog_version:
        host.tool_catalog_version = payload.tool_catalog_version
    if payload.script_catalog_version:
        host.script_catalog_version = payload.script_catalog_version
    host.status = HostStatus.ONLINE.value

    # ADR-0019 Phase 1: count online healthy devices
    online_rows = await db.execute(
        select(Device.id).where(
            Device.host_id == payload.host_id,
            Device.adb_connected == True,
            Device.adb_state.notin_(["offline", "unknown", ""]),
        )
    )
    online_healthy = len(online_rows.scalars().all())

    await db.commit()

    backpressure = await _get_backpressure()
    return ok(HeartbeatResponse(
        tool_catalog_outdated=outdated,
        script_catalog_outdated=scripts_outdated,
        backpressure=BackpressureInfo(log_rate_limit=backpressure),
        capacity={
            "max_concurrent_jobs": host.max_concurrent_jobs,
            "online_healthy_devices": online_healthy,
        },
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
    fencing_token: str  # ADR-0019 Phase 2b: 必填


class _RunCompleteIn(BaseModel):
    update: Dict[str, Any]
    artifact: Optional[Dict[str, Any]] = None
    # Watcher 摘要回填（来自 Agent JobSession.summary.to_complete_payload）
    # 字段形态参考 backend/agent/watcher/contracts.py WatcherSummaryPayload
    # 可选：watcher_id / watcher_started_at / watcher_stopped_at / watcher_capability / log_signal_count / watcher_stats
    watcher_summary: Optional[Dict[str, Any]] = None
    fencing_token: str  # ADR-0019 Phase 2b: 必填


class _ExtendLockIn(BaseModel):
    fencing_token: str  # ADR-0019 Phase 2b: 必填


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
    response: Response = None,
    db: AsyncSession = Depends(get_async_db),
    _=Depends(_verify_agent),
):
    """.. deprecated:: Phase 2c
    Use POST /jobs/claim instead.

    Return PENDING JobInstances for devices on this host (pull model).
    Internally delegates to _claim_jobs_for_host (same logic as POST /jobs/claim).
    """
    # Deprecation headers
    if response is not None:
        response.headers["Deprecation"] = "true"
        response.headers["Sunset"] = "Sat, 01 Nov 2026 00:00:00 GMT"
    logger.info("agent_get_pending_jobs_deprecated host_id=%s", host_id)

    claimed, fencing_token_map = await _claim_jobs_for_host(
        db, host_id, limit,
    )

    if not claimed:
        return ok([])

    # Pre-fetch serial_map for response enrichment
    serial_map: Dict[int, str] = {}
    rows = await db.execute(
        select(Device.id, Device.serial)
        .where(Device.id.in_([j.device_id for j in claimed]))
    )
    serial_map = {row.id: row.serial for row in rows.all()}

    _, watcher_policy_map = await _enrich_job_metadata(db, claimed)

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
            watcher_policy=watcher_policy_map.get(j.id),
            fencing_token=fencing_token_map[j.id],
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

    # ADR-0019 Phase 4b: validate fencing_token via _get_valid_runtime_lease
    valid_lease = await _get_valid_runtime_lease(db, job, payload.fencing_token)
    if valid_lease is None:
        raise HTTPException(status_code=409, detail="invalid or expired fencing_token")

    target = _RUN_TO_JOB.get(payload.status.upper(), JobStatus.RUNNING)
    now = datetime.now(timezone.utc)
    try:
        JobStateMachine.transition(job, target, "agent_heartbeat")
    except InvalidTransitionError:
        pass  # idempotent — already in target state
    if target == JobStatus.RUNNING and job.status == JobStatus.RUNNING.value:
        if not job.started_at:
            job.started_at = now
        job.updated_at = now

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

    # ADR-0019 Phase 4b: fencing_token validation
    if not already_terminal:
        valid_lease = await _get_valid_runtime_lease(db, job, payload.fencing_token)
        if valid_lease is None:
            raise HTTPException(status_code=409, detail="invalid or expired fencing_token")
    else:
        released_lease = (await db.execute(
            select(DeviceLease).where(
                DeviceLease.device_id == job.device_id,
                DeviceLease.job_id == job_id,
                DeviceLease.lease_type == LeaseType.JOB.value,
                DeviceLease.status == LeaseStatus.RELEASED.value,
            )
        )).scalars().first()
        if released_lease is None or released_lease.fencing_token != payload.fencing_token:
            raise HTTPException(status_code=409, detail="invalid or expired fencing_token")

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

    # Watcher 摘要回填（来自 Agent JobSession.summary.to_complete_payload）
    # 字段契约见 backend/agent/watcher/contracts.py WatcherSummaryPayload
    if payload.watcher_summary:
        _apply_watcher_summary(job, payload.watcher_summary)

    if job.status in _TERMINAL:
        await WorkflowAggregator.on_job_terminal(job, db)
        # Release device lease on terminal state (ADR-0019 Phase 2c: device_leases is source of truth)
        released = await release_lease(db, job.device_id, job_id, LeaseType.JOB)
        if not released:
            if already_terminal:
                logger.debug("release_lease_already_released device=%s job=%s", job.device_id, job_id)
            else:
                logger.warning("release_lease_miss device=%s job=%s", job.device_id, job_id)

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
    payload: _ExtendLockIn,
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

    # ADR-0019 Phase 4b: validate fencing_token via _get_valid_runtime_lease
    valid_lease = await _get_valid_runtime_lease(db, job, payload.fencing_token)
    if valid_lease is None:
        raise HTTPException(status_code=409, detail="invalid or expired fencing_token")

    renewed = await extend_lease(db, job.device_id, job_id, LeaseType.JOB, _DEVICE_LOCK_LEASE_SECONDS)
    if not renewed:
        raise HTTPException(status_code=409, detail="device locked by another job")

    now = datetime.now(timezone.utc)
    job.updated_at = now
    await db.commit()
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


# ── Log Signal ingestion ──────────────────────────────────────────────────────

class LogSignalIn(BaseModel):
    """单条 log_signal 信封。

    字段契约见 backend/agent/watcher/contracts.py LogSignalEnvelope。
    幂等键：(job_id, seq_no)
    """
    job_id:         int
    seq_no:         int
    host_id:        str
    device_serial:  str
    category:       str
    source:         str
    path_on_device: str
    detected_at:    str
    artifact_uri:   Optional[str] = None
    sha256:         Optional[str] = None
    size_bytes:     Optional[int] = None
    first_lines:    Optional[str] = None
    extra:          Optional[Dict[str, Any]] = None


class LogSignalBatchIn(BaseModel):
    signals: List[LogSignalIn]


@router.post("/log-signals", response_model=ApiResponse[dict])
async def ingest_log_signals(
    payload: LogSignalBatchIn,
    db: AsyncSession = Depends(get_async_db),
    _=Depends(_verify_agent),
):
    """批量摄取 Agent watcher 采集的异常信号。

    幂等：用 PostgreSQL `ON CONFLICT (job_id, seq_no) DO NOTHING` 去重。
    副作用：按本批实际新插入数累加 job_instance.log_signal_count。
    契约：字段校验见 backend.agent.watcher.contracts.validate_log_signal
    """
    from sqlalchemy import func
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from backend.agent.watcher.contracts import ContractViolation, validate_log_signal

    if not payload.signals:
        return ok({"inserted": 0, "total": 0})

    rows: List[Dict[str, Any]] = []
    for s in payload.signals:
        envelope = s.model_dump()
        try:
            validate_log_signal(envelope)
        except ContractViolation as exc:
            raise HTTPException(status_code=400, detail=f"log_signal contract violation: {exc}")

        # detected_at: ISO string → datetime
        try:
            detected_dt = datetime.fromisoformat(s.detected_at.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"log_signal.detected_at invalid ISO8601: {s.detected_at}",
            )

        rows.append({
            "job_id":         s.job_id,
            "host_id":        s.host_id,
            "device_serial":  s.device_serial,
            "seq_no":         s.seq_no,
            "category":       s.category,
            "source":         s.source,
            "path_on_device": s.path_on_device,
            "artifact_uri":   s.artifact_uri,
            "sha256":         s.sha256,
            "size_bytes":     s.size_bytes,
            "first_lines":    s.first_lines,
            "detected_at":    detected_dt,
            "extra":          s.extra,
        })

    # PostgreSQL 幂等 upsert：ON CONFLICT (job_id, seq_no) DO NOTHING
    stmt = pg_insert(JobLogSignal).values(rows)
    stmt = stmt.on_conflict_do_nothing(index_elements=["job_id", "seq_no"])
    # RETURNING id 以统计实际新增条数（冲突行不返回）
    stmt = stmt.returning(JobLogSignal.id, JobLogSignal.job_id)
    result = await db.execute(stmt)
    inserted_rows = result.all()

    # 按 job 分组累加 log_signal_count
    inserted_count_by_job: Dict[int, int] = {}
    for row in inserted_rows:
        inserted_count_by_job[row.job_id] = inserted_count_by_job.get(row.job_id, 0) + 1

    for jid, count in inserted_count_by_job.items():
        await db.execute(
            JobInstance.__table__.update()
            .where(JobInstance.id == jid)
            .values(log_signal_count=JobInstance.log_signal_count + count)
        )

    await db.commit()
    return ok({"inserted": len(inserted_rows), "total": len(payload.signals)})


async def _get_backpressure() -> Optional[int]:
    """Return current backpressure setting.

    Redis-based backpressure (stp:backpressure:*) removed in Phase 4.
    SocketIO has built-in TCP backpressure; this returns None (no limit).
    Can be extended later with SocketIO-based metrics if needed.
    """
    return None


# ── Artifact ingestion（ADR-0018 5B2）────────────────────────────────────────

# 首期只接受 watcher LogPuller 产出的 crash 实文件 + 可选 bugreport。
# 故意不放开 ANR / MOBILELOG：
#   - ANR / MOBILELOG 在 JobLogSignal 里已经有 path_on_device / first_lines 元数据
#   - 文件本身体量大、价值低，不值得入 JobArtifact 展示/下载通道
_ARTIFACT_TYPE_WHITELIST: set[str] = {"aee_crash", "vendor_aee_crash", "bugreport"}


class ArtifactIn(BaseModel):
    """Agent watcher 上送的单个产物。

    幂等键：(job_id, storage_uri)
    首期边界：artifact_type 必须在 _ARTIFACT_TYPE_WHITELIST 内。
    与 JobLogSignal 解耦：log_signal.artifact_uri 保留为权威指针；
        本端点只负责展示/下载入口的后端持久化。
    """
    storage_uri:           str                       # NFS 路径（已由 Agent LogPuller 落盘）
    artifact_type:         str                       # 白名单
    size_bytes:            Optional[int] = None
    checksum:              Optional[str] = None      # sha256 hex，可选
    source_category:       Optional[str] = None      # AEE | VENDOR_AEE | BUGREPORT（溯源）
    source_path_on_device: Optional[str] = None      # 设备侧原路径（溯源）


class ArtifactOut(BaseModel):
    artifact_id: int
    created:     bool   # True=首次插入；False=幂等命中（已存在同 storage_uri）


@router.post("/jobs/{job_id}/artifacts", response_model=ApiResponse[ArtifactOut])
async def ingest_artifact(
    job_id: int,
    payload: ArtifactIn,
    db: AsyncSession = Depends(get_async_db),
    _=Depends(_verify_agent),
):
    """独立端点：接收 Agent watcher LogPuller 产出的 artifact。

    不复用 /complete：避免把 watcher 异步产物与 Job 终态绑死（Job 在 artifact 上送
    之前/之后终态都合法）。
    幂等：PostgreSQL `ON CONFLICT (job_id, storage_uri) DO NOTHING` —— 重复 POST
    不重复入库，返回已存在的 artifact_id + created=False。
    """
    if not payload.storage_uri:
        raise HTTPException(status_code=400, detail="storage_uri is required")

    if payload.artifact_type not in _ARTIFACT_TYPE_WHITELIST:
        raise HTTPException(
            status_code=400,
            detail=(
                f"artifact_type must be one of {sorted(_ARTIFACT_TYPE_WHITELIST)}; "
                f"got {payload.artifact_type!r}"
            ),
        )

    if payload.size_bytes is not None and payload.size_bytes < 0:
        raise HTTPException(status_code=400, detail="size_bytes must be >= 0")

    job = await db.get(JobInstance, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")

    from sqlalchemy.dialects.postgresql import insert as pg_insert

    stmt = (
        pg_insert(JobArtifact)
        .values(
            job_id=job_id,
            storage_uri=payload.storage_uri,
            artifact_type=payload.artifact_type,
            size_bytes=payload.size_bytes,
            checksum=payload.checksum,
            source_category=payload.source_category,
            source_path_on_device=payload.source_path_on_device,
        )
        .on_conflict_do_nothing(index_elements=["job_id", "storage_uri"])
        .returning(JobArtifact.id)
    )
    res = await db.execute(stmt)
    row = res.first()

    if row is not None:
        # 首次插入
        await db.commit()
        return ok(ArtifactOut(artifact_id=row.id, created=True))

    # 幂等命中 —— 查询已存在的 artifact_id
    existing = await db.execute(
        select(JobArtifact.id)
        .where(
            JobArtifact.job_id == job_id,
            JobArtifact.storage_uri == payload.storage_uri,
        )
    )
    existing_id = existing.scalar_one_or_none()
    if existing_id is None:
        # 极端并发：ON CONFLICT 未返回 id 且 SELECT 也查不到 → 让客户端重试
        logger.warning(
            "artifact_ingest_race job_id=%d storage_uri=%s",
            job_id, payload.storage_uri,
        )
        raise HTTPException(status_code=409, detail="artifact ingest race, please retry")
    await db.commit()
    return ok(ArtifactOut(artifact_id=existing_id, created=False))


def _apply_watcher_summary(job: JobInstance, summary: Dict[str, Any]) -> None:
    """把 Agent 回传的 watcher_summary 回填到 JobInstance 字段。

    字段来源契约：backend/agent/watcher/contracts.py WatcherSummaryPayload
    只在 summary 非空字段存在时覆写，保持旧字段。
    """
    started = summary.get("watcher_started_at")
    if started:
        try:
            job.watcher_started_at = datetime.fromisoformat(started.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            logger.warning("watcher_summary.watcher_started_at invalid: %r", started)

    stopped = summary.get("watcher_stopped_at")
    if stopped:
        try:
            job.watcher_stopped_at = datetime.fromisoformat(stopped.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            logger.warning("watcher_summary.watcher_stopped_at invalid: %r", stopped)

    capability = summary.get("watcher_capability")
    if capability:
        job.watcher_capability = str(capability)[:32]

    # log_signal_count 由 /log-signals 端点累加，这里不覆写
    # 但若 Agent 侧有权威计数（watcher_summary.log_signal_count），作为下限同步
    count = summary.get("log_signal_count")
    if isinstance(count, int) and count > (job.log_signal_count or 0):
        job.log_signal_count = count


# ── ADR-0019 Phase 3a: Recovery Sync ────────────────────────────────────────


@router.post("/recovery/sync", response_model=ApiResponse[dict])
async def recovery_sync(
    payload: _RecoverySyncIn,
    db: AsyncSession = Depends(get_async_db),
    _=Depends(_verify_agent),
):
    """Agent crash-recovery state reconciliation.

    Agent reports local active_jobs + pending_outbox. Backend returns actions
    (RESUME/CLEANUP/ABORT_LOCAL/UPLOAD_TERMINAL/NOOP) to align state.
    """
    # Load host
    host = await db.get(Host, payload.host_id)
    if host is None:
        raise HTTPException(status_code=404, detail="host not found")

    # D1: snapshot previous_boot_id before overwriting
    previous_boot_id = host.boot_id

    # Update host identity
    if payload.boot_id:
        host.boot_id = payload.boot_id
    if payload.agent_instance_id:
        host.last_agent_instance_id = payload.agent_instance_id

    now = datetime.now(timezone.utc)

    # ── Active job actions ──
    _recovery_grace_seconds = 300  # UNKNOWN grace for recovery, matches watchdog
    job_actions: list[_RecoveryAction] = []
    for entry in payload.active_jobs:
        # Query ACTIVE lease for this device+job (Phase 4b: row lock)
        lease = (await db.execute(
            select(DeviceLease).where(
                DeviceLease.device_id == entry.device_id,
                DeviceLease.job_id == entry.job_id,
                DeviceLease.lease_type == LeaseType.JOB.value,
                DeviceLease.status == LeaseStatus.ACTIVE.value,
            ).with_for_update()  # Phase 4b: lock lease row against concurrent Reconciler
        )).scalars().first()

        if lease is None:
            # Also check for RELEASED/EXPIRED lease
            any_lease = (await db.execute(
                select(DeviceLease).where(
                    DeviceLease.device_id == entry.device_id,
                    DeviceLease.job_id == entry.job_id,
                    DeviceLease.lease_type == LeaseType.JOB.value,
                )
            )).scalars().first()
            if any_lease is None:
                job_actions.append(_RecoveryAction(
                    job_id=entry.job_id, device_id=entry.device_id,
                    action="ABORT_LOCAL", reason="no_active_lease",
                ))
            else:
                job_actions.append(_RecoveryAction(
                    job_id=entry.job_id, device_id=entry.device_id,
                    action="ABORT_LOCAL", reason="lease_not_active",
                ))
            continue

        # D3: legacy lease adoption
        lease_agent_id = lease.agent_instance_id or ""

        is_resume = (
            lease_agent_id == payload.agent_instance_id
            or lease_agent_id == payload.host_id
            or not lease_agent_id
            or previous_boot_id == payload.boot_id
        )

        if not is_resume:
            # D7: CLEANUP — boot mismatch → release_lease + job→FAILED
            job = await db.get(JobInstance, entry.job_id)
            if job is not None:
                try:
                    JobStateMachine.transition(job, JobStatus.FAILED, "recovery_cleanup_boot_mismatch")
                except InvalidTransitionError:
                    pass
            await release_lease(db, entry.device_id, entry.job_id, LeaseType.JOB)
            job_actions.append(_RecoveryAction(
                job_id=entry.job_id, device_id=entry.device_id,
                action="CLEANUP", reason="boot_id_mismatch",
            ))
            continue

        # ── RESUME path: check job status for UNKNOWN / terminal edge cases ──
        # Load job with row lock to prevent race with Reconciler
        job = (await db.execute(
            select(JobInstance).where(JobInstance.id == entry.job_id).with_for_update()
        )).scalars().first()

        if job is not None and job.status == JobStatus.UNKNOWN.value:
            # Phase 4b: UNKNOWN→RUNNING resurrection (within grace)
            resumed = await _resume_expired_lease_for_recovery(
                db, lease, job, payload.agent_instance_id, now, _recovery_grace_seconds,
            )
            if resumed:
                try:
                    JobStateMachine.transition(job, JobStatus.RUNNING, "recovery_resume_unknown")
                except InvalidTransitionError:
                    pass
                job_actions.append(_RecoveryAction(
                    job_id=entry.job_id, device_id=entry.device_id,
                    action="RESUME", fencing_token=lease.fencing_token,
                    reason="recovery_resume_unknown",
                ))
            else:
                # Grace expired or state changed under lock
                await release_lease(db, entry.device_id, entry.job_id, LeaseType.JOB)
                job_actions.append(_RecoveryAction(
                    job_id=entry.job_id, device_id=entry.device_id,
                    action="CLEANUP", reason="unknown_grace_expired",
                ))
            continue

        if job is not None and job.status in _TERMINAL:
            # D5: terminal job with lingering ACTIVE lease → release, ABORT_LOCAL
            await release_lease(db, entry.device_id, entry.job_id, LeaseType.JOB)
            job_actions.append(_RecoveryAction(
                job_id=entry.job_id, device_id=entry.device_id,
                action="ABORT_LOCAL", reason="terminal_job_active_lease",
            ))
            continue

        # Normal RESUME: legacy adoption / same_boot update
        if not lease_agent_id or lease_agent_id == payload.host_id:
            lease.agent_instance_id = payload.agent_instance_id
            reason = "legacy_lease_adopted"
        elif previous_boot_id == payload.boot_id and lease_agent_id != payload.agent_instance_id:
            lease.agent_instance_id = payload.agent_instance_id
            reason = "same_boot_instance_updated"
        else:
            reason = "same_instance"

        job_actions.append(_RecoveryAction(
            job_id=entry.job_id, device_id=entry.device_id,
            action="RESUME", fencing_token=lease.fencing_token,
            reason=reason,
        ))

    # ── Outbox actions ──
    outbox_actions: list[_RecoveryAction] = []
    for entry in payload.pending_outbox:
        job = await db.get(JobInstance, entry.job_id)
        if job is None:
            outbox_actions.append(_RecoveryAction(
                job_id=entry.job_id,
                action="NOOP", reason="job_not_found",
            ))
        elif job.status in _TERMINAL:
            outbox_actions.append(_RecoveryAction(
                job_id=entry.job_id,
                action="NOOP", reason="already_terminal",
            ))
        else:
            outbox_actions.append(_RecoveryAction(
                job_id=entry.job_id,
                action="UPLOAD_TERMINAL", event_type=entry.event_type,
                reason="not_terminal_on_backend",
            ))

    await db.commit()

    logger.info(
        "recovery_sync host=%s jobs=%d outbox=%d actions_job=%d actions_outbox=%d",
        payload.host_id,
        len(payload.active_jobs), len(payload.pending_outbox),
        len(job_actions), len(outbox_actions),
    )

    return ok({
        "actions": [a.model_dump() for a in job_actions],
        "outbox_actions": [a.model_dump() for a in outbox_actions],
    })
