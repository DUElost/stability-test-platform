"""Agent API: job claim, status update, step trace upload, heartbeat.

Authentication: X-Agent-Secret header (compared to AGENT_SECRET env var).
"""

import json
import hashlib
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import case, func, or_, select, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.response import ApiResponse, err, ok
from backend.api.error_helpers import raise_api_http_error
from backend.core.agent_secret import AgentSecretNotConfiguredError, require_agent_secret
from backend.core.audit import record_audit_async
from backend.core.artifact_paths import ArtifactPathError, resolve_local_artifact_path
from backend.core.database import get_async_db
from backend.core.metrics import (
    claim_lease_failed_total,
    post_completion_enqueue_failed_total,
    record_log_signal_ingested,
    record_patrol_heartbeat,
    record_reconciler_skip_unchanged,
    record_watcher_capability,
)
from backend.models.enums import HostStatus, JobStatus, LeaseStatus, LeaseType
from backend.models.host import Device, Host
from backend.models.device_lease import DeviceLease
from backend.models.job import JobArtifact, JobInstance, JobLogSignal, StepTrace
from backend.api.routes.auth import get_current_active_user
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun
from backend.realtime.socketio_server import broadcast_plan_run_status, broadcast_run_job_update
from backend.services.aggregator import PlanAggregator
from backend.services.lease_manager import acquire_lease, extend_lease, release_lease
from backend.services.plan_dispatcher_core import (
    apply_dispatch_host_watcher_admin_state_to_policy,
    extract_dispatch_host_watcher_admin_states,
)
from backend.services.reconciler import reconcile_step_traces
from backend.services.state_machine import InvalidTransitionError, JobStateMachine

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/agent", tags=["agent"])

_DEVICE_LOCK_LEASE_SECONDS = int(os.getenv("DEVICE_LOCK_LEASE_SECONDS", "600"))
_TERMINAL = {
    JobStatus.COMPLETED.value,
    JobStatus.FAILED.value,
    JobStatus.ABORTED.value,
}
# NOTE: UNKNOWN is intentionally excluded — it is a transient recovery state,
# not a terminal one.  Valid transitions are UNKNOWN→RUNNING (grace recovery)
# or UNKNOWN→FAILED (grace expiry).  ``complete_job()``'s runtime-lease gate
# (``_get_valid_runtime_lease``, default ``allowed_job_statuses={RUNNING}``)
# rejects direct completion while UNKNOWN — the agent must re-sync via recovery
# (UNKNOWN→RUNNING) before completing normally.  This also prevents premature
# PlanRun aggregation while a job's true status is still unresolved.


class _LockAcquireFailed(Exception):
    """Raised inside a savepoint when device lock acquire fails."""
    pass


def _verify_agent(x_agent_secret: Optional[str] = Header(None, alias="X-Agent-Secret")):
    # secrets.compare_digest 防时序攻击。
    try:
        expected = require_agent_secret()
    except AgentSecretNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    provided = x_agent_secret or ""
    if not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="invalid agent secret")


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


async def _enrich_job_metadata(
    db: AsyncSession, jobs: List[JobInstance],
) -> tuple[Dict[int, str], Dict[int, Optional[Dict[str, Any]]]]:
    """批量获取 JobOut 需要的 device_serial + watcher_policy。

    返回：
        serial_map:         device_id -> serial
        watcher_policy_map: job_id    -> watcher_policy (来自 PlanRun.plan_snapshot)
    空 jobs 返回 ({}, {})，避免空集合上的 IN ()（PostgreSQL 会报语法错误）。
    """
    if not jobs:
        return {}, {}

    device_ids = [j.device_id for j in jobs]
    serial_rows = await db.execute(
        select(Device.id, Device.serial).where(Device.id.in_(device_ids))
    )
    serial_map = {row.id: row.serial for row in serial_rows.all()}

    plan_run_ids = {j.plan_run_id for j in jobs if j.plan_run_id is not None}
    watcher_admin_snapshot_by_run: Dict[int, Dict[str, bool]] = {}
    watcher_policy_by_run: Dict[int, Optional[dict]] = {}
    if plan_run_ids:
        snapshot_rows = await db.execute(
            select(
                PlanRun.id,
                PlanRun.run_context,
                PlanRun.plan_snapshot,
            ).where(PlanRun.id.in_(plan_run_ids))
        )
        for row in snapshot_rows.all():
            watcher_admin_snapshot_by_run[row.id] = (
                extract_dispatch_host_watcher_admin_states(row.run_context)
            )
            snapshot_plan = (
                (row.plan_snapshot or {}).get("plan", {})
                if isinstance(row.plan_snapshot, dict)
                else {}
            )
            watcher_policy_by_run[row.id] = snapshot_plan.get("watcher_policy")

    watcher_policy_map = {
        j.id: apply_dispatch_host_watcher_admin_state_to_policy(
            watcher_policy_by_run.get(j.plan_run_id)
            if j.plan_run_id is not None
            else None,
            host_id=j.host_id,
            dispatch_host_watcher_admin_states=(
                watcher_admin_snapshot_by_run.get(j.plan_run_id)
                if j.plan_run_id is not None
                else None
            ),
        )
        for j in jobs
    }

    return serial_map, watcher_policy_map


async def _build_recovery_job_payload(
    db: AsyncSession,
    job: JobInstance,
    *,
    device_serial: str,
    fencing_token: str,
) -> Dict[str, Any]:
    """Build the minimal claim-shaped payload required for Agent resume execution.

    NOTE: PlanRun is fetched individually here (not batched with other jobs).
    Recovery is a single-job path in practice; if batch recovery is introduced later,
    consider pre-loading PlanRun rows upstream and passing dispatch_host_watcher_admin_states
    as a parameter to avoid N+1 queries.
    """
    watcher_policy = None
    dispatch_host_watcher_admin_states: Dict[str, bool] = {}
    if job.plan_run_id is not None:
        plan_run = await db.get(PlanRun, job.plan_run_id)
        if plan_run is not None:
            snapshot_plan = (
                (plan_run.plan_snapshot or {}).get("plan", {})
                if isinstance(plan_run.plan_snapshot, dict)
                else {}
            )
            watcher_policy = snapshot_plan.get("watcher_policy")
            dispatch_host_watcher_admin_states = (
                extract_dispatch_host_watcher_admin_states(plan_run.run_context)
            )
    watcher_policy = apply_dispatch_host_watcher_admin_state_to_policy(
        watcher_policy,
        host_id=job.host_id,
        dispatch_host_watcher_admin_states=dispatch_host_watcher_admin_states,
    )

    return {
        "id": job.id,
        "plan_run_id": job.plan_run_id,
        "plan_id": job.plan_id,
        "device_id": job.device_id,
        "device_serial": device_serial,
        "host_id": job.host_id,
        "status": job.status,
        "pipeline_def": job.pipeline_def,
        "watcher_policy": watcher_policy,
        "fencing_token": fencing_token,
    }


# ── Schemas ───────────────────────────────────────────────────────────────────

class ClaimRequest(BaseModel):
    host_id: str
    capacity: int = 10
    agent_instance_id: str = ""   # ADR-0019 Phase 3a
    agent_version: str = ""


class JobOut(BaseModel):
    id: int
    plan_run_id: Optional[int] = None
    plan_id: Optional[int] = None
    device_id: int
    device_serial: Optional[str] = None
    host_id: Optional[str]
    status: str
    pipeline_def: dict
    # Watcher 策略覆盖（来自 Plan.watcher_policy）
    # Agent 解析见 backend/agent/watcher/policy.py WatcherPolicy.from_job
    watcher_policy: Optional[Dict[str, Any]] = None
    fencing_token: str  # ADR-0019 Phase 2b: 必填，来自 DeviceLease.fencing_token


class JobStatusUpdate(BaseModel):
    status: str
    reason: str = ""
    fencing_token: str


class StepTraceIn(BaseModel):
    job_id: int
    step_id: str
    stage: str = "execute"
    event_type: str
    status: str = ""
    output: Optional[str] = None
    error_message: Optional[str] = None
    original_ts: Optional[str] = None
    trace_event_id: Optional[str] = None
    fencing_token: str


class HeartbeatRequest(BaseModel):
    host_id: str
    script_catalog_version: str = ""
    load: Dict[str, Any] = {}
    capacity: Optional[Dict[str, Any]] = None  # ADR-0019 Phase 1
    agent_instance_id: str = ""   # ADR-0019 Phase 3a
    boot_id: str = ""             # ADR-0019 Phase 3a


class BackpressureInfo(BaseModel):
    log_rate_limit: Optional[int]


class HeartbeatResponse(BaseModel):
    script_catalog_outdated: bool = False
    backpressure: BackpressureInfo
    capacity: Optional[Dict[str, Any]] = None  # ADR-0019 Phase 1
    agent_min_version: str = ""  # SemVer floor; Agent refuses to run if below


# ── ADR-0019 Phase 3a: Recovery Sync models ──────────────────────────────────


class _ActiveJobEntry(BaseModel):
    job_id: int
    device_id: int
    device_serial: Optional[str] = None
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
    device_serial: str = ""
    job_payload: Optional[Dict[str, Any]] = None
    event_type: str = ""
    reason: str = ""


class _RecoverySyncOut(BaseModel):
    data: dict  # {"actions": [...], "outbox_actions": [...]}


# ── Shared helpers ────────────────────────────────────────────────────────────


def _version_tuple(value: str) -> tuple[int, ...]:
    raw = (value or "").strip()
    if "-" in raw:
        raise ValueError("pre-release Agent versions are not supported")
    core = raw.split("+", 1)[0]
    parts = core.split(".")
    if len(parts) != 3 or any(not part.isdigit() for part in parts):
        raise ValueError(f"invalid semantic version: {value!r}")
    return tuple(int(part) for part in parts)


def _agent_version_is_supported(agent_version: str, minimum: str) -> bool:
    from backend.services.agent_version_gate import agent_version_is_supported
    return agent_version_is_supported(agent_version, minimum)


async def _claim_jobs_for_host(
    db: AsyncSession,
    host_id: str,
    capacity: int = 10,
    agent_instance_id: str = "",
) -> tuple[List[JobInstance], Dict[int, str]]:
    """Shared claim logic for claim_jobs (POST) and get_pending_jobs (GET).

    Phase 2d hardening:
    - Host row FOR UPDATE serializes concurrent claims for the same host
    - Capacity comes from the Agent's reported value; real cap is the free healthy device count (93b9935 removed the host slot limit)
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
    if host_row.status != HostStatus.ONLINE.value:
        await db.rollback()
        return [], {}

    # 2. Effective capacity — each device runs at most 1 Job;
    #    Agent's "capacity" is the soft cap on concurrent jobs.
    #    Defensive clamp: never claim more than free device count.
    effective_capacity = capacity

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

    effective_capacity = min(effective_capacity, len(free_device_ids))

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
            .join(PlanRun, PlanRun.id == JobInstance.plan_run_id)
            .where(
                JobInstance.device_id.in_(free_device_ids),
                JobInstance.status == JobStatus.PENDING.value,
                PlanRun.status == "RUNNING",
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
                    claim_lease_failed_total.inc()
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
    allowed_job_statuses: set[str] | None = None,
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
    expires_at = _as_utc(lease.expires_at)
    if expires_at is None or expires_at <= now:
        return None
    # C: only RUNNING jobs may perform runtime operations
    allowed_statuses = allowed_job_statuses or {JobStatus.RUNNING.value}
    if job.status not in allowed_statuses:
        return None
    return lease


async def _require_valid_runtime_lease(
    db: AsyncSession,
    job: JobInstance,
    fencing_token: str,
) -> DeviceLease:
    valid_lease = await _get_valid_runtime_lease(db, job, fencing_token)
    if valid_lease is None:
        raise HTTPException(status_code=409, detail="invalid or expired fencing_token")
    return valid_lease


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


async def _rotate_recovery_lease_token(
    db: AsyncSession,
    lease: DeviceLease,
    *,
    agent_instance_id: str,
) -> str:
    """Fence the previous local worker when a new Agent instance takes over."""
    device = (await db.execute(
        select(Device)
        .where(Device.id == lease.device_id)
        .with_for_update()
    )).scalars().first()
    if device is None:
        raise HTTPException(status_code=409, detail="recovery device not found")
    device.lease_generation = int(device.lease_generation or 0) + 1
    lease.lease_generation = device.lease_generation
    lease.fencing_token = f"{lease.device_id}:{device.lease_generation}"
    lease.agent_instance_id = agent_instance_id
    lease.renewed_at = datetime.now(timezone.utc)
    return lease.fencing_token


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
    from backend.services.agent_version_gate import (
        agent_version_is_supported,
        resolve_agent_min_version,
    )

    minimum_version = resolve_agent_min_version()
    if minimum_version and not agent_version_is_supported(
        payload.agent_version, minimum_version,
    ):
        raise HTTPException(
            status_code=426,
            detail={
                "code": "AGENT_UPGRADE_REQUIRED",
                "agent_version": payload.agent_version,
                "minimum_version": minimum_version,
            },
        )

    claimed, fencing_token_map = await _claim_jobs_for_host(
        db, payload.host_id, payload.capacity, payload.agent_instance_id,
    )

    if not claimed:
        return ok([])

    # Enrich response: device_serial + watcher_policy(from Plan)
    serial_map, watcher_policy_map = await _enrich_job_metadata(db, claimed)

    return ok([
        JobOut(
            id=j.id, plan_run_id=j.plan_run_id,
            plan_id=j.plan_id, device_id=j.device_id,
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

    await _require_valid_runtime_lease(db, job, payload.fencing_token)

    try:
        new_status = JobStatus(payload.status.upper())
    except ValueError:
        raise HTTPException(status_code=400, detail=f"unknown status: {payload.status}")

    if new_status in {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.ABORTED}:
        raise_api_http_error(
            status_code=409,
            code="TERMINAL_STATUS_REQUIRES_COMPLETE",
            message="terminal status must be reported through /jobs/{job_id}/complete",
        )
    if new_status != JobStatus.RUNNING:
        raise_api_http_error(
            status_code=409,
            code="INVALID_JOB_TRANSITION",
            message="status endpoint only accepts RUNNING",
        )

    # Compatibility endpoint is now heartbeat-only.  Claim already performs
    # PENDING→RUNNING atomically with lease acquisition, so repeated RUNNING is
    # a no-op rather than a second state transition.
    job.updated_at = datetime.now(timezone.utc)
    if payload.reason:
        job.status_reason = payload.reason

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
    for trace in traces:
        job = await db.get(JobInstance, trace.job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        await _require_valid_runtime_lease(db, job, trace.fencing_token)

    raw = [t.model_dump() for t in traces]
    result = await reconcile_step_traces(host_id, raw, db)

    # Push job_status / plan_run_status for transitioned jobs (B5)
    for tj_id in result["transitioned_jobs"]:
        job = await db.get(JobInstance, tj_id)
        if job is not None:
            await broadcast_run_job_update(job.plan_run_id, tj_id, job.status)
            pr = await db.get(PlanRun, job.plan_run_id)
            if pr is not None and pr.status in {
                "SUCCESS", "PARTIAL_SUCCESS", "FAILED", "DEGRADED",
            }:
                await broadcast_plan_run_status(pr.id, pr.status)

    return ok({"inserted": result["inserted"], "total": len(traces)})


@router.post("/heartbeat", response_model=ApiResponse[HeartbeatResponse])
async def agent_heartbeat(
    payload: HeartbeatRequest,
    db: AsyncSession = Depends(get_async_db),
    _=Depends(_verify_agent),
):
    """
    Update host last_heartbeat.
    Returns script_catalog_outdated flag + current backpressure setting.
    """
    host = await db.get(Host, payload.host_id)
    if host is None:
        host = Host(
            id=payload.host_id,
            hostname=payload.host_id,
            status=HostStatus.ONLINE.value,
            created_at=datetime.now(timezone.utc),
        )
        db.add(host)

    scripts_outdated = (
        bool(payload.script_catalog_version)
        and bool(host.script_catalog_version)
        and host.script_catalog_version != payload.script_catalog_version
    )

    host.last_heartbeat = datetime.now(timezone.utc)
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
    from backend.services.agent_version_gate import resolve_agent_min_version
    return ok(HeartbeatResponse(
        script_catalog_outdated=scripts_outdated,
        backpressure=BackpressureInfo(log_rate_limit=backpressure),
        capacity={
            "online_healthy_devices": online_healthy,
        },
        agent_min_version=resolve_agent_min_version(),
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
    fencing_token: str


# ── Compat endpoints (mirroring old /runs/* interface under /jobs/*) ──────────


@router.get("/jobs/pending", response_model=ApiResponse[List[JobOut]])
async def get_pending_jobs(
    host_id: str,
    limit: int = 10,
    response: Response = None,
    db: AsyncSession = Depends(get_async_db),
    _=Depends(_verify_agent),
):
    """Removed by the one-shot Agent protocol switch; use POST /jobs/claim."""
    raise HTTPException(
        status_code=410,
        detail={
            "code": "LEGACY_CLAIM_ENDPOINT_REMOVED",
            "message": "use POST /api/v1/agent/jobs/claim",
        },
    )


@router.post("/jobs/{job_id}/heartbeat", response_model=ApiResponse[dict])
async def job_heartbeat(
    job_id: int,
    payload: _JobHeartbeatIn,
    db: AsyncSession = Depends(get_async_db),
    _=Depends(_verify_agent),
):
    """Keep an already claimed RUNNING job alive."""
    job = await db.get(JobInstance, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")

    # ADR-0019 Phase 4b: validate fencing_token via _get_valid_runtime_lease
    valid_lease = await _get_valid_runtime_lease(
        db,
        job,
        payload.fencing_token,
        allowed_job_statuses={JobStatus.RUNNING.value},
    )
    if valid_lease is None:
        raise HTTPException(status_code=409, detail="invalid or expired fencing_token")

    target = _RUN_TO_JOB.get(payload.status.upper(), JobStatus.RUNNING)
    if target != JobStatus.RUNNING:
        raise_api_http_error(
            status_code=409,
            code="TERMINAL_STATUS_REQUIRES_COMPLETE",
            message="job heartbeat cannot finalize a job; use /complete",
        )
    now = datetime.now(timezone.utc)
    if job.status == JobStatus.RUNNING.value:
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
    job = (await db.execute(
        select(JobInstance)
        .where(JobInstance.id == job_id)
        .with_for_update()
    )).scalars().first()
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")

    raw = str(payload.update.get("status", "FAILED")).upper()
    target = _RUN_TO_JOB.get(raw, JobStatus.FAILED)
    if target not in {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.ABORTED}:
        raise HTTPException(
            status_code=400,
            detail={"code": "INVALID_TERMINAL_STATUS", "requested_status": raw},
        )

    completion_fact = {
        "update": payload.update,
        "artifact": payload.artifact,
        "watcher_summary": payload.watcher_summary,
    }
    payload_digest = hashlib.sha256(
        json.dumps(
            completion_fact,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()

    # Terminal replay is strictly read-only.  Validate against the historical
    # lease token so a stale/cross-host Agent cannot rewrite completion facts.
    already_terminal = job.status in _TERMINAL
    if already_terminal:
        historical_lease = (await db.execute(
            select(DeviceLease)
            .where(
                DeviceLease.device_id == job.device_id,
                DeviceLease.job_id == job.id,
                DeviceLease.lease_type == LeaseType.JOB.value,
            )
            .order_by(DeviceLease.id.desc())
        )).scalars().first()
        if (
            historical_lease is None
            or historical_lease.fencing_token != payload.fencing_token
        ):
            current_status = job.status
            await record_audit_async(
                db,
                action="stale_job_completion_rejected",
                resource_type="job",
                resource_id=job.id,
                details={
                    "plan_run_id": job.plan_run_id,
                    "current_status": current_status,
                    "reason": "historical_fencing_token_mismatch",
                },
                username="agent",
            )
            await db.commit()
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "STALE_COMPLETION_TOKEN",
                    "current_status": current_status,
                },
            )
        expected_digest = job.terminal_payload_digest
        if expected_digest is None:
            historical_trace = (await db.execute(
                select(StepTrace).where(
                    StepTrace.job_id == job_id,
                    StepTrace.step_id == "__job__",
                    StepTrace.event_type == "RUN_COMPLETE",
                )
            )).scalars().first()
            if historical_trace is not None and historical_trace.output:
                try:
                    historical_fact = json.loads(historical_trace.output)
                    current_legacy_fact = {
                        "update": payload.update,
                        "artifact": payload.artifact,
                    }
                    expected_digest = hashlib.sha256(
                        json.dumps(
                            historical_fact,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode("utf-8")
                    ).hexdigest()
                    replay_digest = hashlib.sha256(
                        json.dumps(
                            current_legacy_fact,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode("utf-8")
                    ).hexdigest()
                except (TypeError, ValueError, json.JSONDecodeError):
                    replay_digest = ""
            else:
                replay_digest = ""
        else:
            replay_digest = payload_digest
        if not expected_digest or not secrets.compare_digest(
            expected_digest, replay_digest,
        ):
            current_status = job.status
            await record_audit_async(
                db,
                action="terminal_payload_conflict",
                resource_type="job",
                resource_id=job.id,
                details={
                    "plan_run_id": job.plan_run_id,
                    "current_status": current_status,
                    "expected_digest": expected_digest,
                    "received_digest": replay_digest or payload_digest,
                },
                username="agent",
            )
            await db.commit()
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "TERMINAL_PAYLOAD_CONFLICT",
                    "current_status": current_status,
                },
            )
        current_status = job.status
        await db.rollback()
        return ok({"job_id": job_id, "status": current_status, "idempotent": True})
    else:
        valid_lease = None
        if job.status == JobStatus.UNKNOWN.value:
            # Explicit late-terminal reconciliation: a matching grace-held
            # token may atomically restore UNKNOWN→RUNNING before completion.
            # This preserves the terminal outbox fact without adding the
            # forbidden UNKNOWN→COMPLETED edge to the state machine.
            candidate = (await db.execute(
                select(DeviceLease)
                .where(
                    DeviceLease.device_id == job.device_id,
                    DeviceLease.job_id == job.id,
                    DeviceLease.lease_type == LeaseType.JOB.value,
                    DeviceLease.status == LeaseStatus.ACTIVE.value,
                )
                .with_for_update()
            )).scalars().first()
            if (
                candidate is not None
                and secrets.compare_digest(
                    candidate.fencing_token, payload.fencing_token,
                )
                and await _resume_expired_lease_for_recovery(
                    db,
                    candidate,
                    job,
                    candidate.agent_instance_id,
                    datetime.now(timezone.utc),
                )
            ):
                JobStateMachine.transition(
                    job, JobStatus.RUNNING, "late_completion_recovery",
                )
                valid_lease = candidate
        if valid_lease is None:
            valid_lease = await _get_valid_runtime_lease(
                db, job, payload.fencing_token,
            )
        if valid_lease is None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "INVALID_OR_EXPIRED_FENCING_TOKEN",
                    "current_status": job.status,
                },
            )

    transition_from_status = job.status
    try:
        JobStateMachine.transition(job, target, payload.update.get("error_message") or "")
    except InvalidTransitionError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "INVALID_JOB_TRANSITION",
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
    if existing_snapshot is None:
        db.add(
            StepTrace(
                job_id=job_id,
                step_id="__job__",
                stage="post_process",
                status=target.value,
                event_type="RUN_COMPLETE",
                output=snapshot_output,
                error_message=payload.update.get("error_message"),
                trace_event_id=f"terminal:{job_id}:{payload_digest}",
                original_ts=now_ts,
                created_at=datetime.now(timezone.utc),
            )
        )

    job.ended_at = datetime.now(timezone.utc)
    job.terminal_payload_digest = payload_digest

    # Watcher 摘要回填（来自 Agent JobSession.summary.to_complete_payload）
    # 字段契约见 backend/agent/watcher/contracts.py WatcherSummaryPayload
    if payload.watcher_summary:
        _apply_watcher_summary(job, payload.watcher_summary)

    if target == JobStatus.ABORTED:
        plan_run = (await db.execute(
            select(PlanRun)
            .where(PlanRun.id == job.plan_run_id)
            .with_for_update(key_share=True)
        )).scalars().first()
        if plan_run is not None and isinstance(plan_run.run_context, dict):
            run_context = dict(plan_run.run_context)
            abort_request = dict(run_context.get("abort_requested") or {})
            acknowledged = list(
                abort_request.get("acknowledged_job_ids") or []
            )
            if job.id not in acknowledged:
                acknowledged.append(job.id)
            abort_request["acknowledged_job_ids"] = acknowledged
            run_context["abort_requested"] = abort_request
            plan_run.run_context = run_context

    if job.status in _TERMINAL:
        # M0/Task2: 仅在首次终态桥接 reconciler 计数,避免 outbox 重试重复计数。
        if payload.watcher_summary:
            _bridge_reconciler_metrics(job.host_id, payload.watcher_summary)
            # M4/T4-2: 终态时按 watcher_capability 自增一次(覆盖率监控盘);
            # 与 reconciler 桥接同处 not-already_terminal 守卫内,每 Job 仅计一次。
            record_watcher_capability(job.watcher_capability or "unknown")
        # Release before aggregation.  Chain dispatch inherits the same devices
        # and must observe them as free when the terminal transaction commits.
        released = await release_lease(db, job.device_id, job_id, LeaseType.JOB)
        if not released:
            logger.warning("release_lease_miss device=%s job=%s", job.device_id, job_id)
        await db.flush()
        await record_audit_async(
            db,
            action="job_terminalized",
            resource_type="job",
            resource_id=job.id,
            details={
                "plan_run_id": job.plan_run_id,
                "from_status": transition_from_status,
                "to_status": target.value,
                "payload_digest": payload_digest,
                "lease_released": bool(released),
            },
            username="agent",
        )
        await PlanAggregator.on_job_terminal(job, db)

    await db.commit()

    if job.status in _TERMINAL:
        # ── SocketIO push: job completed/failed → notify PlanRun subscribers ──
        await broadcast_run_job_update(job.plan_run_id, job_id, job.status)
        run = await db.get(PlanRun, job.plan_run_id)
        if run is not None and run.status in {
            "SUCCESS", "PARTIAL_SUCCESS", "FAILED", "DEGRADED",
        }:
            await broadcast_plan_run_status(run.id, run.status)

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
            post_completion_enqueue_failed_total.inc()
            logger.error("post_completion enqueue failed for job %d: %s", job_id, e)

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


# ── Host-level batch lease renewal (P0 scale reduction) ─────────────────────
#
# Why: at 60 host × ~17 device the per-job extend_lock loop is ~17 serial HTTP
# calls/host every renewal interval (~17 req/s fleet-wide) AND the Agent's
# LeaseRenewer renews jobs one-by-one, so a slow control plane serially delays
# every renewal and can push leases past TTL → mass UNKNOWN. This endpoint
# collapses one host's renewals into a single request whose items are
# independently classified: one stale token never fails the rest of the batch.
#
# Result contract is strictly per-item: renewed / stale_token / job_not_running
# / lease_missing. Set-based validation + a single guarded UPDATE keep cost O(1)
# in round-trips regardless of batch size; RETURNING makes each response reflect
# what the UPDATE actually renewed (so a lease released by the reconciler between
# SELECT and UPDATE downgrades to lease_missing rather than a false "renewed").

_LEASE_EXTEND_BATCH_MAX = int(os.getenv("AGENT_LEASE_EXTEND_BATCH_MAX", "200"))


class _ExtendBatchItemIn(BaseModel):
    job_id: int
    fencing_token: str
    # P1 forward-compat (invariant ③, three independent signals): execution_state
    # + progress_marker ride along on the same renewal request so the Agent wire
    # contract is stable now. The backing JobInstance columns land in P1; until
    # then these are accepted and ignored (no second Agent migration needed).
    # progress_marker is a structured snapshot (e.g. {"patrol_cycle_index": N,
    # "last_progress_at": "..."}), so it is typed as an open dict, not a string.
    execution_state: Optional[str] = None
    progress_marker: Optional[Dict[str, Any]] = None


class _ExtendBatchIn(BaseModel):
    host_id: str
    agent_instance_id: str = ""
    leases: List[_ExtendBatchItemIn]


class _ExtendBatchItemOut(BaseModel):
    job_id: int
    # renewed | stale_token | job_not_running | lease_missing
    status: str
    expires_at: Optional[str] = None


class _ExtendBatchOut(BaseModel):
    results: List[_ExtendBatchItemOut]


async def _cas_renew_leases(
    db: AsyncSession,
    *,
    pairs: List[tuple],
    host_id: str,
    agent_instance_id: str,
    now: datetime,
    new_expires: datetime,
) -> set[int]:
    """Final ownership CAS for batch renewal. Returns job_ids actually renewed.

    The caller's prelim classification validated a SNAPSHOT; between that
    SELECT and this UPDATE the lease can be released and re-acquired (token
    rotated, e.g. recovery takeover). Matching by job_id alone would let the
    OLD Agent renew the NEW owner's lease. So this UPDATE re-asserts the full
    ownership tuple at write time:
      - (job_id, fencing_token) pair-bound via row-value IN
      - host binding; agent-instance binding when the Agent reports one
      - ACTIVE + expires_at > now (never revive a grace-held lease)
      - Job.status == RUNNING join (a job that went terminal concurrently
        must not get a fresh TTL)
    Does NOT commit — runs inside the caller's transaction.
    """
    conditions = [
        tuple_(DeviceLease.job_id, DeviceLease.fencing_token).in_(pairs),
        DeviceLease.lease_type == LeaseType.JOB.value,
        DeviceLease.status == LeaseStatus.ACTIVE.value,
        DeviceLease.expires_at > now,  # Phase 4b: refuse expired lease
        DeviceLease.host_id == host_id,
        JobInstance.id == DeviceLease.job_id,  # UPDATE .. FROM job_instance
        JobInstance.status == JobStatus.RUNNING.value,
    ]
    if agent_instance_id:
        conditions.append(DeviceLease.agent_instance_id == agent_instance_id)
    renewed_rows = (await db.execute(
        update(DeviceLease)
        .where(*conditions)
        .values(renewed_at=now, expires_at=new_expires)
        .returning(DeviceLease.job_id)
        .execution_options(synchronize_session=False)
    )).all()
    return {row.job_id for row in renewed_rows}


@router.post("/leases/extend-batch", response_model=ApiResponse[_ExtendBatchOut])
async def extend_leases_batch(
    payload: _ExtendBatchIn,
    db: AsyncSession = Depends(get_async_db),
    _=Depends(_verify_agent),
):
    """Renew every ACTIVE JOB lease this host still owns, in one request.

    Per-item outcome (mirrors the single-job ``extend_lock`` gate,
    ``_get_valid_runtime_lease``):
      - ``job_not_running``: job is missing or ``status != RUNNING`` — the Agent
        should stop renewing it and drive ``/agent/recovery/sync``.
      - ``lease_missing``: no ACTIVE JOB lease, or the lease is expired
        (grace-held) — the reconciler owns expiry, this path never revives it.
      - ``stale_token``: an ACTIVE lease exists but the fencing_token does not
        match — this Agent has been fenced.
      - ``renewed``: TTL extended; ``expires_at`` is the new deadline.
    """
    now = datetime.now(timezone.utc)
    items = payload.leases
    if not items:
        return ok(_ExtendBatchOut(results=[]))
    if len(items) > _LEASE_EXTEND_BATCH_MAX:
        raise HTTPException(
            status_code=413,
            detail={
                "code": "LEASE_BATCH_TOO_LARGE",
                "max": _LEASE_EXTEND_BATCH_MAX,
                "received": len(items),
            },
        )

    # Preserve request order; duplicate job_ids collapse to ONE result entry and
    # the LAST occurrence's token wins (the later token is the more recent claim
    # a well-behaved Agent knows about). Documented + tested — not an error.
    ordered_job_ids: List[int] = []
    token_by_job: Dict[int, str] = {}
    for it in items:
        if it.job_id not in token_by_job:
            ordered_job_ids.append(it.job_id)
        token_by_job[it.job_id] = it.fencing_token

    job_rows = (await db.execute(
        select(JobInstance.id, JobInstance.status).where(
            JobInstance.id.in_(ordered_job_ids)
        )
    )).all()
    job_status = {row.id: row.status for row in job_rows}

    lease_rows = (await db.execute(
        select(
            DeviceLease.job_id,
            DeviceLease.fencing_token,
            DeviceLease.expires_at,
            DeviceLease.host_id,
            DeviceLease.agent_instance_id,
        )
        .where(
            DeviceLease.job_id.in_(ordered_job_ids),
            DeviceLease.lease_type == LeaseType.JOB.value,
            DeviceLease.status == LeaseStatus.ACTIVE.value,
        )
    )).all()
    lease_by_job = {row.job_id: row for row in lease_rows}

    prelim: Dict[int, str] = {}
    renewable_job_ids: List[int] = []
    for jid in ordered_job_ids:
        if job_status.get(jid) != JobStatus.RUNNING.value:
            prelim[jid] = "job_not_running"
            continue
        lease = lease_by_job.get(jid)
        if lease is None:
            prelim[jid] = "lease_missing"
            continue
        if lease.fencing_token != token_by_job[jid]:
            prelim[jid] = "stale_token"
            continue
        # Ownership binding: the lease must belong to the requesting host (and
        # agent instance when the Agent reports one). A token leaked across
        # hosts/instances must not renew — classified as fenced.
        if lease.host_id != payload.host_id:
            prelim[jid] = "stale_token"
            continue
        if payload.agent_instance_id and lease.agent_instance_id != payload.agent_instance_id:
            prelim[jid] = "stale_token"
            continue
        expires_at = _as_utc(lease.expires_at)
        if expires_at is None or expires_at <= now:
            # Expired ACTIVE (grace-held) lease: the reconciler is the sole owner
            # of expiry; batch renewal must not revive it (parity with the single
            # endpoint's expires_at>now gate).
            prelim[jid] = "lease_missing"
            continue
        prelim[jid] = "renewable"
        renewable_job_ids.append(jid)

    new_expires = now + timedelta(seconds=_DEVICE_LOCK_LEASE_SECONDS)
    renewed_ids: set[int] = set()
    if renewable_job_ids:
        cas_pairs = [(jid, token_by_job[jid]) for jid in renewable_job_ids]
        renewed_ids = await _cas_renew_leases(
            db,
            pairs=cas_pairs,
            host_id=payload.host_id,
            agent_instance_id=payload.agent_instance_id,
            now=now,
            new_expires=new_expires,
        )
        if renewed_ids:
            # Mirror the single endpoint: a renewal is also a RUNNING keepalive,
            # so refresh job.updated_at to hold off the recycler heartbeat
            # timeout. RUNNING guard: never bump the liveness anchor of a job
            # that reached a terminal state after the lease CAS above.
            await db.execute(
                update(JobInstance)
                .where(
                    JobInstance.id.in_(renewed_ids),
                    JobInstance.status == JobStatus.RUNNING.value,
                )
                .values(updated_at=now)
                .execution_options(synchronize_session=False)
            )
    await db.commit()

    results: List[_ExtendBatchItemOut] = []
    for jid in ordered_job_ids:
        state = prelim[jid]
        if state == "renewable":
            if jid in renewed_ids:
                results.append(_ExtendBatchItemOut(
                    job_id=jid, status="renewed",
                    expires_at=new_expires.isoformat(),
                ))
            else:
                # CAS miss: released/expired/token-rotated/job-terminal between
                # SELECT and UPDATE. Report a lost status (not renewed) so the
                # Agent recovers instead of trusting a phantom TTL.
                results.append(_ExtendBatchItemOut(job_id=jid, status="lease_missing"))
        else:
            results.append(_ExtendBatchItemOut(job_id=jid, status=state))

    return ok(_ExtendBatchOut(results=results))


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
    job = await db.get(JobInstance, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    await _require_valid_runtime_lease(db, job, payload.fencing_token)

    result = await reconcile_step_traces("agent", [trace], db)

    # Push job_status / plan_run_status if the job transitioned (B5)
    for tj_id in result["transitioned_jobs"]:
        job = await db.get(JobInstance, tj_id)
        if job is not None:
            await broadcast_run_job_update(job.plan_run_id, tj_id, job.status)
            pr = await db.get(PlanRun, job.plan_run_id)
            if pr is not None and pr.status in {
                "SUCCESS", "PARTIAL_SUCCESS", "FAILED", "DEGRADED",
            }:
                await broadcast_plan_run_status(pr.id, pr.status)

    return ok({"job_id": job_id, "step_id": step_id, "status": payload.status})


# ── ADR-0022: Patrol Heartbeat ────────────────────────────────────────────────


class PatrolHeartbeatIn(BaseModel):
    """ADR-0022 D3: per-cycle patrol aggregation upload (no step_trace written).

    Contract:
      cycle_index: monotonic; server uses MAX(existing, payload) so out-of-order
        heartbeats do not regress the cycle counter.
      success_delta / failed_delta: positive integers added to the running totals
        in the same UPDATE.  Either may be 0.  Agent should send delta=1 per
        cycle in the normal path (success XOR failure).
      current_step: best-effort; UI uses it for the device matrix.
      current_failure_streak: Agent-computed value (server overwrites the column).
      next_retry_at: ISO8601 string; null when not in backoff.
      watcher_capability: optional watcher capability live snapshot.
      manual_action_observed: optional echo-back: when Agent has consumed a
        RETRY_NOW or EXIT_REQUESTED, it sends the value here so the server can
        clear the column atomically.
    """

    fencing_token: str
    cycle_index: int
    success_delta: int = 0
    failed_delta: int = 0
    current_step: Optional[str] = None
    current_failure_streak: int = 0
    next_retry_at: Optional[str] = None
    watcher_capability: Optional[str] = None
    manual_action_observed: Optional[str] = None


class PatrolHeartbeatOut(BaseModel):
    """Mirror of the post-update job_instance patrol fields, plus pending
    manual_action so the Agent can short-circuit its sleep loop without a
    separate poll endpoint.
    """

    job_id: int
    patrol_cycle_count: int
    patrol_success_cycle_count: int
    patrol_failed_cycle_count: int
    current_failure_streak: int
    next_retry_at: Optional[str] = None
    manual_action: Optional[str] = None  # pending action for Agent to honor


@router.post(
    "/jobs/{job_id}/patrol-heartbeat",
    response_model=ApiResponse[PatrolHeartbeatOut],
)
async def patrol_heartbeat(
    job_id: int,
    payload: PatrolHeartbeatIn,
    db: AsyncSession = Depends(get_async_db),
    _=Depends(_verify_agent),
):
    """ADR-0022 D2/D3: receive a patrol cycle aggregate, update job_instance
    counter columns atomically, return current pending manual_action.

    Does NOT write to step_trace.  Out-of-order safe: cycle_count is monotonic
    via GREATEST().  Empty deltas are accepted (pure heartbeat / mid-cycle ping).
    """
    job = await db.get(JobInstance, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")

    # ADR-0022 D10: Job 已非 RUNNING(典型:recycler 已把 status 推到 UNKNOWN)→
    # 直接 409 JOB_NOT_RUNNING,与 L1033 CAS 失配的契约统一。本 slice 仅落 backend
    # ground truth;Agent 端如何消费此 code(理想:停 patrol 循环并触发 /recovery/sync)
    # 留给下一 slice — 当前 patrol_heartbeat_uploader.py 收到 409 只 log + return None,
    # lease-lost 收口由 LeaseRenewer (lease_renewer.py:152-167) 通过续租 409/404
    # 触发 _on_lease_lost 兜底完成。
    if job.status != JobStatus.RUNNING.value:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "JOB_NOT_RUNNING",
                "message": (
                    f"Job {job_id} status={job.status} (not RUNNING); "
                    "trigger /agent/recovery/sync to re-establish lease before next patrol cycle"
                ),
            },
        )

    await _require_valid_runtime_lease(db, job, payload.fencing_token)

    if payload.success_delta < 0 or payload.failed_delta < 0:
        raise HTTPException(status_code=400, detail="delta must be non-negative")
    if payload.cycle_index < 0:
        raise HTTPException(status_code=400, detail="cycle_index must be non-negative")
    if payload.current_failure_streak < 0:
        raise HTTPException(status_code=400, detail="current_failure_streak must be non-negative")

    next_retry_dt = None
    if payload.next_retry_at:
        try:
            next_retry_dt = datetime.fromisoformat(payload.next_retry_at.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(status_code=400, detail=f"invalid next_retry_at: {payload.next_retry_at}")

    now = datetime.now(timezone.utc)

    # Atomic UPDATE — out-of-order heartbeats use GREATEST() to avoid regression.
    update_values: Dict[str, Any] = {
        "patrol_cycle_count":         func.greatest(JobInstance.patrol_cycle_count, payload.cycle_index),
        "patrol_success_cycle_count": JobInstance.patrol_success_cycle_count + payload.success_delta,
        "patrol_failed_cycle_count":  JobInstance.patrol_failed_cycle_count + payload.failed_delta,
        "current_failure_streak":     payload.current_failure_streak,
        "next_retry_at":              next_retry_dt,
        "last_patrol_heartbeat_at":   now,
        "updated_at":                 now,
    }
    if payload.current_step is not None:
        update_values["current_patrol_step"] = payload.current_step
    capability = (payload.watcher_capability or "").strip()
    if capability:
        update_values["watcher_capability"] = capability[:32]

    # If Agent reports it consumed/observed a manual_action, clear it —
    # but ONLY when DB.manual_action still equals what Agent observed.
    # Why: 否则用户在 Agent observed → heartbeat 抵达之间二次点击 / 切换 (RETRY_NOW↔EXIT_REQUESTED)
    #      会被无条件清除静默吞掉,新意图永远不会被 Agent 看到。SQL CASE 让清除变成"DB 没改 → 清,
    #      DB 已是新意图 → 原样保留",与 ADR-0022 D7 manual_action 单字段语义一致。
    if payload.manual_action_observed:
        update_values["manual_action"] = case(
            (
                JobInstance.manual_action == payload.manual_action_observed,
                None,
            ),
            else_=JobInstance.manual_action,
        )

    # ADR-0022 D10: 写侧 CAS — status='RUNNING' guard 防御「预校验通过、CAS 阶段
    # recycler patrol_stall pass 在 _require_valid_runtime_lease 通过后才 commit」
    # 的 race。0 行返回 → 与改动 A 同 code 同语义,统一 JOB_NOT_RUNNING 出口。
    result = await db.execute(
        update(JobInstance)
        .where(
            JobInstance.id == job_id,
            JobInstance.status == JobStatus.RUNNING.value,
        )
        .values(**update_values)
        .returning(JobInstance.id)
    )
    if result.first() is None:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail={
                "code": "JOB_NOT_RUNNING",
                "message": (
                    f"Job {job_id} status flipped during patrol-heartbeat write; "
                    "trigger /agent/recovery/sync to re-establish lease before next patrol cycle"
                ),
            },
        )
    await db.commit()

    record_patrol_heartbeat(
        failed_delta=payload.failed_delta,
        current_failure_streak=payload.current_failure_streak,
    )

    # Re-fetch to return canonical values + any pending manual_action newly set.
    # Use explicit column selection to avoid lazy-load / MissingGreenlet issues
    # on async PostgreSQL when expire_on_commit fires.
    result = await db.execute(
        select(
            JobInstance.patrol_cycle_count,
            JobInstance.patrol_success_cycle_count,
            JobInstance.patrol_failed_cycle_count,
            JobInstance.current_failure_streak,
            JobInstance.next_retry_at,
            JobInstance.manual_action,
        ).where(JobInstance.id == job_id)
    )
    row = result.one()
    return ok(PatrolHeartbeatOut(
        job_id=job_id,
        patrol_cycle_count=row.patrol_cycle_count or 0,
        patrol_success_cycle_count=row.patrol_success_cycle_count or 0,
        patrol_failed_cycle_count=row.patrol_failed_cycle_count or 0,
        current_failure_streak=row.current_failure_streak or 0,
        next_retry_at=_iso_or_none(row.next_retry_at),
        manual_action=row.manual_action,
    ))


def _iso_or_none(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    return _as_utc(dt).isoformat()


# ── Log Signal ingestion ──────────────────────────────────────────────────────

class LogSignalIn(BaseModel):
    """单条 log_signal 信封。

    字段契约见 backend/agent/watcher/contracts.py LogSignalEnvelope。
    幂等键：(job_id, seq_no)
    """
    job_id:         int
    fencing_token:  str
    agent_instance_id: str
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
        job = await db.get(JobInstance, s.job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"job {s.job_id} not found")
        await _require_job_bound_upload_lease(
            db,
            job,
            fencing_token=s.fencing_token,
            agent_instance_id=s.agent_instance_id,
            host_id=s.host_id,
            device_serial=s.device_serial,
        )

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
    # RETURNING id + (job_id, seq_no, category) 以统计实际新增条数 + Prometheus 分类
    stmt = stmt.returning(
        JobLogSignal.id,
        JobLogSignal.job_id,
        JobLogSignal.seq_no,
        JobLogSignal.category,
    )
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

    # ── Prometheus 埋点:仅对实际入库的 signal 计数(冲突丢弃的不计) ──
    for row in inserted_rows:
        record_log_signal_ingested(row.category)

    # ── ADR-0021 C5c: 推 watcher_signal 增量到 plan_run room ──
    # 事件作为 invalidation hint 使用,前端收到后 refetch /watcher-summary。
    # 失败不影响入库结果(socket 服务未起 / 未连前端时静默)。
    if inserted_rows:
        try:
            from backend.realtime.socketio_server import broadcast_watcher_signal

            job_ids = list({row.job_id for row in inserted_rows})
            run_map_rows = (
                (
                    await db.execute(
                        select(JobInstance.id, JobInstance.plan_run_id)
                        .where(JobInstance.id.in_(job_ids))
                    )
                ).all()
            )
            run_id_by_job: Dict[int, int] = {
                jid: rid for jid, rid in run_map_rows if rid is not None
            }
            # Build a lookup from the original payload for device_serial enrichment.
            serial_by_seq: Dict[tuple, Optional[str]] = {
                (s.job_id, s.seq_no): s.device_serial for s in payload.signals
            }
            for row in inserted_rows:
                run_id = run_id_by_job.get(row.job_id)
                if run_id is None:
                    continue
                await broadcast_watcher_signal(
                    run_id,
                    job_id=row.job_id,
                    device_serial=serial_by_seq.get((row.job_id, row.seq_no)),
                    category=row.category,
                    inserted_count=1,
                )
        except Exception:
            logger.debug("broadcast_watcher_signal_failed", exc_info=True)

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
    fencing_token:         str
    agent_instance_id:     str
    host_id:               str
    device_serial:         str
    size_bytes:            Optional[int] = None
    checksum:              Optional[str] = None      # sha256 hex，可选
    source_category:       Optional[str] = None      # AEE | VENDOR_AEE | BUGREPORT（溯源）
    source_path_on_device: Optional[str] = None      # 设备侧原路径（溯源）


class ArtifactOut(BaseModel):
    artifact_id: int
    created:     bool   # True=首次插入；False=幂等命中（已存在同 storage_uri）


async def _require_job_bound_upload_lease(
    db: AsyncSession,
    job: JobInstance,
    *,
    fencing_token: str,
    agent_instance_id: str,
    host_id: str,
    device_serial: str,
) -> DeviceLease:
    """Authorize active and delayed terminal uploads by historical token."""
    lease = (await db.execute(
        select(DeviceLease)
        .where(
            DeviceLease.job_id == job.id,
            DeviceLease.device_id == job.device_id,
            DeviceLease.lease_type == LeaseType.JOB.value,
            DeviceLease.fencing_token == fencing_token,
        )
        .order_by(DeviceLease.id.desc())
    )).scalars().first()
    device = await db.get(Device, job.device_id)
    if (
        lease is None
        or device is None
        or lease.host_id != host_id
        or job.host_id != host_id
        or (
            job.status not in _TERMINAL
            and device.host_id != host_id
        )
        or lease.agent_instance_id != agent_instance_id
        or (device.serial or "").strip() != (device_serial or "").strip()
    ):
        raise HTTPException(
            status_code=409,
            detail={"code": "UPLOAD_FENCING_MISMATCH"},
        )
    return lease


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
    try:
        resolve_local_artifact_path(payload.storage_uri, must_exist=False)
    except ArtifactPathError as exc:
        raise_api_http_error(
            status_code=400,
            code="INVALID_ARTIFACT_PATH",
            message=(
                "artifact path is invalid or outside the allowed root "
                f"(STP_NFS_ROOT/STP_WATCHER_NFS_BASE_DIR): {exc}"
            ),
        )

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
    await _require_job_bound_upload_lease(
        db,
        job,
        fencing_token=payload.fencing_token,
        agent_instance_id=payload.agent_instance_id,
        host_id=payload.host_id,
        device_serial=payload.device_serial,
    )

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


def _bridge_reconciler_metrics(host_id: Optional[str], summary: Dict[str, Any]) -> None:
    """M0/Task2: 把 Agent 进程内的 reconciler 计数桥接到中心 /metrics。

    Agent 没有独立的 Prometheus /metrics 暴露面,reconciler 的
    `reconciler_skip_unchanged_total` 只在 Agent 进程的本地 registry 自增、永不被抓取。
    为让 M4 监控盘可见,Agent 通过 complete 通道带出整个 Job 生命周期累计的
    `reconciler_stats.ticks_skipped_unchanged`,后端在 Job *首次*进入终态时一次性
    按该累计值自增中心计数器(每个 Job 仅贡献一次 → 计数器单调正确)。

    `reconciler_burst_mode_active` 是运行期实时 gauge(Job 结束时恒为 0),无法通过
    终态快照有意义地带出 → 仍仅 Agent 进程内,详见 §2.3 文档说明。
    """
    stats = summary.get("reconciler_stats")
    if not isinstance(stats, dict):
        return
    skipped = stats.get("ticks_skipped_unchanged")
    if isinstance(skipped, int) and skipped > 0:
        record_reconciler_skip_unchanged(str(host_id or "unknown"), amount=skipped)


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
        # Lock the complete ownership tuple.  Shared AGENT_SECRET authenticates
        # an Agent process, not a host/job relationship; the fencing token and
        # relational checks below establish that relationship.
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

        job = (await db.execute(
            select(JobInstance)
            .where(JobInstance.id == entry.job_id)
            .with_for_update()
        )).scalars().first()
        device = (await db.execute(
            select(Device)
            .where(Device.id == entry.device_id)
            .with_for_update()
        )).scalars().first()
        actual_serial = ((device.serial or "").strip() if device is not None else "")
        reported_serial = (entry.device_serial or "").strip()
        ownership_valid = (
            job is not None
            and device is not None
            and job.device_id == entry.device_id
            and job.host_id == payload.host_id
            and lease.host_id == payload.host_id
            and device.host_id == payload.host_id
            and bool(entry.fencing_token)
            and secrets.compare_digest(entry.fencing_token, lease.fencing_token)
            and (not reported_serial or actual_serial == reported_serial)
        )
        if not ownership_valid:
            logger.warning(
                "recovery_sync_ownership_rejected host=%s job=%s device=%s",
                payload.host_id, entry.job_id, entry.device_id,
            )
            job_actions.append(_RecoveryAction(
                job_id=entry.job_id,
                device_id=entry.device_id,
                action="ABORT_LOCAL",
                reason="recovery_ownership_mismatch",
            ))
            continue

        lease_agent_id = lease.agent_instance_id or ""
        boot_matches = (
            previous_boot_id == payload.boot_id
            if previous_boot_id and payload.boot_id
            else lease_agent_id == payload.agent_instance_id
        )

        if not boot_matches:
            # Host reboot invalidates the local process.  Finalize through the
            # same terminal side effects expected from normal completion.
            try:
                JobStateMachine.transition(
                    job, JobStatus.FAILED, "recovery_cleanup_boot_mismatch",
                )
                job.ended_at = now
            except InvalidTransitionError:
                pass
            await release_lease(db, entry.device_id, entry.job_id, LeaseType.JOB)
            await db.flush()
            if job.status == JobStatus.FAILED.value:
                await PlanAggregator.on_job_terminal(job, db)
            job_actions.append(_RecoveryAction(
                job_id=entry.job_id, device_id=entry.device_id,
                action="CLEANUP", reason="boot_id_mismatch",
            ))
            continue

        if job.status == JobStatus.UNKNOWN.value:
            # Phase 4b: UNKNOWN→RUNNING resurrection (within grace)
            resumed = await _resume_expired_lease_for_recovery(
                db, lease, job, payload.agent_instance_id, now, _recovery_grace_seconds,
            )
            if resumed:
                if lease_agent_id != payload.agent_instance_id:
                    await _rotate_recovery_lease_token(
                        db, lease, agent_instance_id=payload.agent_instance_id,
                    )
                try:
                    JobStateMachine.transition(job, JobStatus.RUNNING, "recovery_resume_unknown")
                except InvalidTransitionError:
                    pass
                job_payload = await _build_recovery_job_payload(
                    db,
                    job,
                    device_serial=actual_serial,
                    fencing_token=lease.fencing_token,
                )
                job_actions.append(_RecoveryAction(
                    job_id=entry.job_id, device_id=entry.device_id,
                    action="RESUME", fencing_token=lease.fencing_token,
                    device_serial=actual_serial,
                    job_payload=job_payload,
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

        if job.status in _TERMINAL:
            # D5: terminal job with lingering ACTIVE lease → release, ABORT_LOCAL
            await release_lease(db, entry.device_id, entry.job_id, LeaseType.JOB)
            job_actions.append(_RecoveryAction(
                job_id=entry.job_id, device_id=entry.device_id,
                action="ABORT_LOCAL", reason="terminal_job_active_lease",
            ))
            continue

        if job.status != JobStatus.RUNNING.value:
            job_actions.append(_RecoveryAction(
                job_id=entry.job_id, device_id=entry.device_id,
                action="ABORT_LOCAL", reason=f"job_not_resumable:{job.status}",
            ))
            continue

        # Repeated sync from the same live instance must not launch a second
        # local worker.  A new instance on the same boot receives a rotated
        # token so the old worker is fenced before RESUME is returned.
        if lease_agent_id == payload.agent_instance_id:
            job_actions.append(_RecoveryAction(
                job_id=entry.job_id,
                device_id=entry.device_id,
                action="NOOP",
                fencing_token=lease.fencing_token,
                device_serial=actual_serial,
                reason="same_instance_worker_already_owned",
            ))
            continue

        await _rotate_recovery_lease_token(
            db, lease, agent_instance_id=payload.agent_instance_id,
        )
        reason = "same_boot_instance_takeover"

        job_payload = await _build_recovery_job_payload(
            db,
            job,
            device_serial=actual_serial,
            fencing_token=lease.fencing_token,
        )
        job_actions.append(_RecoveryAction(
            job_id=entry.job_id, device_id=entry.device_id,
            action="RESUME", fencing_token=lease.fencing_token,
            device_serial=actual_serial,
            job_payload=job_payload,
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


@router.get("/{host_id}/archive-status")
async def get_archive_status(
    host_id: str,
    db: AsyncSession = Depends(get_async_db),
    _user=Depends(get_current_active_user),
):
    """ADR-0025 Sprint 3: 控制面查看某 host 的存储运维概览。

    数据源：Agent 心跳上报的运维指标（Host.extra['archive']）+
    系统指标（Host.extra['capacity'] / Host.extra['health']）。
    scan 状态占位（Sprint 4）。
    """
    host = await db.get(Host, host_id)
    if host is None:
        raise HTTPException(status_code=404, detail="host not found")

    extra = host.extra if isinstance(host.extra, dict) else {}

    return ok({
        "host_id": host_id,
        "agent_metrics": extra.get("archive"),
        "capacity": extra.get("capacity"),
        "health": extra.get("health"),
        "agent_version": extra.get("agent_version"),
        "scan_status": None,
        "scan_triggered_at": None,
    })
