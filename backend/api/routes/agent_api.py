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

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import case, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from backend.api.response import ApiResponse, ok
from backend.api.error_helpers import raise_api_http_error
from backend.core.agent_secret import AgentSecretNotConfiguredError, require_agent_secret
from backend.core.audit import record_audit
from backend.core.artifact_paths import (
    ArtifactPathError,
    resolve_local_artifact_path,
)
from backend.core.database import get_async_db, get_db
from backend.core.metrics import (
    record_patrol_heartbeat,
)
from backend.models.enums import HostStatus, JobStatus, LeaseType
from backend.models.host import Device, Host
from backend.models.device_lease import DeviceLease
from backend.models.job import JobArtifact, JobInstance
from backend.api.routes.auth import get_current_active_user
from backend.models.plan_run import PlanRun
from backend.services.agent_recovery import (
    _RecoverySyncIn,
    sync_agent_recovery,
)
# 既有测试 / 外部导入的 recovery 符号仍从本模块可达（re-export）。
from backend.services.agent_recovery import (  # noqa: F401
    _ActiveJobEntry,
    _OutboxEntry,
    _RecoveryAction,
    _RecoverySyncOut,
    _build_recovery_job_payload,
    _rotate_recovery_lease_token,
)
from backend.services.agent_lease_extend import (
    _ExtendBatchIn,
    _ExtendBatchOut,
    _VALID_EXECUTION_STATES,
    _parse_progress_ts,
    extend_agent_leases_batch,
)
from backend.services.agent_claim import (  # noqa: F401
    ClaimRequest,
    JobOut,
    LockAcquireFailed as _LockAcquireFailed,
    _claim_jobs_for_host,
    _enrich_job_metadata,
    claim_agent_jobs,
    claim_jobs_for_host,
    enrich_job_metadata,
)
from backend.services.agent_device_log_events import (
    DeviceLogEventBatchIn,
    ingest_agent_device_log_events,
    list_agent_device_log_events,
)
from backend.services.agent_device_log_events import (  # noqa: F401
    DeviceLogEventIn,
    _ALLOWED_TRANSITIONS,
    _EXTRACTABLE_STATES,
    _TRANSITIONS_LITERAL,
    _VALID_EVENT_STATES,
    _parse_iso_dt,
    _validated_remote_path,
)
from backend.services.agent_log_signals import (
    LogSignalBatchIn,
    ingest_agent_log_signals,
)
from backend.services.agent_log_signals import (  # noqa: F401
    LogSignalIn,
    _TERMINAL,
    _require_job_bound_upload_lease,
    require_job_bound_upload_lease,
)
from backend.services.agent_lease_extend import (  # noqa: F401
    _ExtendBatchItemIn,
    _ExtendBatchItemOut,
    _LEASE_EXTEND_BATCH_MAX,
    _cas_renew_leases,
)
from backend.services.agent_completion import (
    _RUN_TO_JOB,
    _RunCompleteIn,
    _get_valid_runtime_lease,
    complete_agent_job,
)
from backend.services.agent_completion import (  # noqa: F401
    _apply_watcher_summary,
    _bridge_reconciler_metrics,
)
from backend.realtime.socketio_server import broadcast_plan_run_status, broadcast_run_job_update
from backend.services.host_maintenance import HostMaintenanceConflict
from backend.services.host_retirement import (
    retired_heartbeat_context,
    should_alert_retired_heartbeat,
)
from backend.services.host_upgrade_gate import (
    HostAbortDrainTimeoutError,
    HostAbortPendingError,
    HostHasActiveJobsError,
    HostNotFoundError,
    HostRetiredError,
    begin_host_upgrade,
    end_host_upgrade,
)
from backend.services.lease_manager import extend_lease
from backend.services.reconciler import reconcile_step_traces
from backend.services.script_catalog_version import compute_script_catalog_version_async

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/agent", tags=["agent"])

_DEVICE_LOCK_LEASE_SECONDS = int(os.getenv("DEVICE_LOCK_LEASE_SECONDS", "600"))
# NOTE: UNKNOWN is intentionally excluded — it is a transient recovery state,
# not a terminal one.  Valid transitions are UNKNOWN→RUNNING (grace recovery)
# or UNKNOWN→FAILED (grace expiry).  ``complete_job()``'s runtime-lease gate
# (``_get_valid_runtime_lease``, default ``allowed_job_statuses={RUNNING}``)
# rejects direct completion while UNKNOWN — the agent must re-sync via recovery
# (UNKNOWN→RUNNING) before completing normally.  This also prevents premature
# PlanRun aggregation while a job's true status is still unresolved.



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




# ── Schemas ───────────────────────────────────────────────────────────────────

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
    exit_code: Optional[int] = None
    metadata: Optional[dict] = None
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
    log_rate_limit: Optional[int] = None
    # ADR-0026 P0: suggested Agent poll interval (seconds)
    heartbeat_interval_seconds: Optional[int] = None


class HeartbeatResponse(BaseModel):
    script_catalog_outdated: bool = False
    backpressure: BackpressureInfo
    capacity: Optional[Dict[str, Any]] = None  # ADR-0019 Phase 1
    agent_min_version: str = ""  # SemVer floor; Agent refuses to run if below
    heartbeat_interval_seconds: Optional[int] = None

# ── ADR-0019 Phase 3a: Recovery Sync models ──────────────────────────────────


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



# ── ADR-0019 Phase 4b: Runtime Lease Validation ────────────────────────────────


async def _require_valid_runtime_lease(
    db: AsyncSession,
    job: JobInstance,
    fencing_token: str,
) -> DeviceLease:
    valid_lease = await _get_valid_runtime_lease(db, job, fencing_token)
    if valid_lease is None:
        raise HTTPException(status_code=409, detail="invalid or expired fencing_token")
    return valid_lease


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
    return ok(await claim_agent_jobs(db, payload))


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
        raise HTTPException(status_code=400, detail=f"unknown status: {payload.status}") from None

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
                "SUCCESS", "PARTIAL_SUCCESS", "FAILED",
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

    # Compare the Agent's cached catalog against **the control plane's current
    # one**, not against whatever this Agent reported last time. The old
    # self-comparison could only ever fire when the Agent changed, so publishing
    # a new script version never reached a running Agent — the mistake showed up
    # much later as ScriptVersionMismatch at job execution time.
    scripts_outdated = bool(payload.script_catalog_version) and (
        payload.script_catalog_version
        != await compute_script_catalog_version_async(db)
    )

    host.last_heartbeat = datetime.now(timezone.utc)
    if payload.script_catalog_version:
        host.script_catalog_version = payload.script_catalog_version
    # ADR-0038 §1.2 归属声明：轻量心跳与权威 `/api/v1/heartbeat` **共用**
    # `should_alert_retired_heartbeat` 同一判据（“共用检测”选项，非双通道
    # 收敛）——退役心跳的「如实记录 + 保持退役 + 单次告警」两条路径同源。
    prev_status = host.status
    host.status = HostStatus.ONLINE.value

    if should_alert_retired_heartbeat(host, prev_status=prev_status):
        from backend.services.notification_service import dispatch_notification_async

        dispatch_notification_async(
            "HOST_RETIRED_HEARTBEAT", retired_heartbeat_context(host),
        )

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
    # Light agent heartbeat: scale interval with online healthy device count
    # (same contract as /api/v1/heartbeat — ADR-0026 P0).
    from backend.api.routes.heartbeat import (
        _suggested_heartbeat_interval,
        _suggested_log_rate_limit,
    )
    suggested_interval = _suggested_heartbeat_interval(online_healthy)
    suggested_log_rate = (
        backpressure if backpressure is not None
        else _suggested_log_rate_limit(online_healthy)
    )
    from backend.services.agent_version_gate import resolve_agent_min_version
    return ok(HeartbeatResponse(
        script_catalog_outdated=scripts_outdated,
        backpressure=BackpressureInfo(
            log_rate_limit=suggested_log_rate,
            heartbeat_interval_seconds=suggested_interval,
        ),
        capacity={
            "online_healthy_devices": online_healthy,
        },
        agent_min_version=resolve_agent_min_version(),
        heartbeat_interval_seconds=suggested_interval,
    ))


# ── RunStatus→JobStatus mapping for compat endpoints ─────────────────────────


class _JobHeartbeatIn(BaseModel):
    status: str = "RUNNING"
    started_at: Optional[str] = None
    fencing_token: str  # ADR-0019 Phase 2b: 必填


class _ExtendLockIn(BaseModel):
    fencing_token: str  # ADR-0019 Phase 2b: 必填


class _StepStatusIn(BaseModel):
    status: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    exit_code: Optional[int] = None
    error_message: Optional[str] = None
    metadata: Optional[dict] = None
    trace_event_id: Optional[str] = None
    fencing_token: str



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
    return ok(await complete_agent_job(db, job_id, payload))


@router.post("/jobs/{job_id}/extend_lock", response_model=ApiResponse[dict])
async def extend_job_lock(
    job_id: int,
    payload: _ExtendLockIn,
    db: AsyncSession = Depends(get_async_db),
    _=Depends(_verify_agent),
):
    """Extend device lock lease for a running job."""
    # #1980：先锁 Job 行，再碰 Lease —— 与 complete_job / extend_leases_batch /
    # _reconcile_expired_leases 保持同一全序（Job → Lease）。原先用 db.get 无锁读 Job，
    # 随后 extend_lease 先 UPDATE device_leases、直到 job.updated_at 才 UPDATE
    # job_instance，即 Lease → Job；与上述路径交错会形成环路等待。
    job = (await db.execute(
        select(JobInstance)
        .where(JobInstance.id == job_id)
        .with_for_update()
    )).scalars().first()
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
# ── Batch lease renew (ADR-0019 / ADR-0026) ───────────────────────────────


@router.post("/leases/extend-batch", response_model=ApiResponse[_ExtendBatchOut])
async def extend_leases_batch(
    payload: _ExtendBatchIn,
    db: AsyncSession = Depends(get_async_db),
    _=Depends(_verify_agent),
):
    """Renew every ACTIVE JOB lease this host still owns, in one request.

    业务逻辑见 ``backend.services.agent_lease_extend.extend_agent_leases_batch``。
    """
    return ok(await extend_agent_leases_batch(db, payload))


# ── ADR-0026 Step 5b: per-host Coordinator heartbeat ─────────────────────────
# The HostRunCoordinator (Agent-side, per PlanRunHost projection) reports the
# host it manages + every active job's current execution_state. Epoch fencing
# prevents a stale coordinator (process restart) from overwriting a new one.


class _CoordinatorHeartbeatJob(BaseModel):
    job_id: int
    execution_state: Optional[str] = None
    last_progress_at: Optional[str] = None  # ISO8601


class _CoordinatorHeartbeatIn(BaseModel):
    host_id: str
    agent_instance_id: str
    # coordinator_epoch is now inside each plan_run_hosts entry (Step 5b收口 #10).
    # A single top-level epoch would incorrectly share one epoch across hosts.
    plan_run_hosts: List[dict]  # [{id, plan_run_id, host_id, coordinator_epoch, phase}]
    jobs: List[_CoordinatorHeartbeatJob] = []


class _CoordinatorHeartbeatOut(BaseModel):
    accepted: bool
    stale_plan_run_host_ids: List[int] = []  # epoch was already higher → Agent must reconcile
    agent_instance_stale: bool = False  # host already bound to a newer agent instance
    current_coordinator_epochs: Dict[int, int] = {}  # prh_id → control-plane epoch


_VALID_COORDINATOR_PHASES = {
    "INIT",
    "BARRIER_WAIT",
    "PATROL",
    "TEARDOWN",
}


@router.post("/coordinator-heartbeat", response_model=ApiResponse[_CoordinatorHeartbeatOut])
async def coordinator_heartbeat(
    payload: _CoordinatorHeartbeatIn,
    db: AsyncSession = Depends(get_async_db),
    _=Depends(_verify_agent),
):
    """ADR-0026 Step 5b: per-host Coordinator liveness + per-job state sync.

    - Rejects heartbeats from a superseded ``agent_instance_id`` (host already
      bound to a newer Agent process) so the old Coordinator self-fences.
    - Bumps coordinator_heartbeat_at for every PlanRunHost in the payload
      where coordinator_epoch >= stored epoch (epoch fencing).
    - Persists execution_state + last_progress_at for every listed job
      (RUNNING-guarded).
    - Returns which PlanRunHost rows were stale (higher epoch already seen)
      so the Agent can reconcile (terminate that Coordinator instance).
    """
    from backend.models.host import Host
    from backend.models.plan_run import PlanRunHost as _PRH

    now = datetime.now(timezone.utc)
    stale_host_ids: list[int] = []
    current_epochs: dict[int, int] = {}

    # Agent-instance fencing: a restarted Agent claims a new instance id via
    # host heartbeat; an old Coordinator process must not rewrite projections.
    host = await db.get(Host, payload.host_id)
    if host is not None:
        stored_instance = (host.last_agent_instance_id or "").strip()
        reported_instance = (payload.agent_instance_id or "").strip()
        if stored_instance and reported_instance and stored_instance != reported_instance:
            for entry in payload.plan_run_hosts:
                prh_id = entry.get("id")
                if not prh_id:
                    continue
                row = await db.get(_PRH, prh_id)
                if row is None:
                    continue
                stale_host_ids.append(int(prh_id))
                current_epochs[int(prh_id)] = int(row.coordinator_epoch or 0)
            logger.warning(
                "coord_hb_agent_instance_stale host=%s stored=%s reported=%s prh=%s",
                payload.host_id, stored_instance, reported_instance, stale_host_ids,
            )
            return ok(_CoordinatorHeartbeatOut(
                accepted=False,
                agent_instance_stale=True,
                stale_plan_run_host_ids=stale_host_ids,
                current_coordinator_epochs=current_epochs,
            ))

    # #1980：先写 job_instance，再写 plan_run_host —— 与终态化路径保持同一全序。
    # complete_job / 回收器先持 job 行锁，再由 on_job_terminal → _bump_host_counters
    # 更新 plan_run_host（job_instance → plan_run_host）。本端点原先相反：先改
    # PlanRunHost（ORM 变更在后续语句的 autoflush 里落库），再 UPDATE job_instance，
    # 于是与终态化形成环路等待（`coordinator_heartbeat` 持 prh 行等 job 行，终态化
    # 持 job 行等 prh 行）。两个循环互相独立，交换顺序即可；下面的
    # `db.execute(update(JobInstance))` 会先执行并锁住 job 行，plan_run_host 的变更
    # 随后才 flush。
    for j in payload.jobs:
        reported = j.execution_state
        state_val = reported if reported in _VALID_EXECUTION_STATES else None
        # ADR-0026 §3 clock discipline (#288):
        # - WAITING_*/PATROL_SLEEP: refresh waiting clock (and never EXECUTING).
        # - EXECUTING_STEP: persist state only — execution hb comes from
        #   extend-batch.
        # - Unknown/null state: nothing to persist.
        # - Whenever we do write, updated_at is pinned (never bumped): the
        #   recycler judges liveness solely by the execution signals.
        values: dict[str, Any] = {}
        if state_val is not None:
            values["execution_state"] = state_val
        if state_val in ("WAITING_EXECUTION_SLOT", "PATROL_SLEEP", "WAITING_BARRIER"):
            values["last_execution_heartbeat_at"] = now
        if not values:
            continue
        values["updated_at"] = JobInstance.updated_at
        ts = _parse_progress_ts(j.last_progress_at)
        if ts is not None:
            values["last_progress_at"] = ts
        await db.execute(
            update(JobInstance)
            .where(
                JobInstance.id == j.job_id,
                JobInstance.host_id == payload.host_id,
                JobInstance.status == JobStatus.RUNNING.value,
            )
            .values(**values)
            .execution_options(synchronize_session=False)
        )

    for entry in payload.plan_run_hosts:
        prh_id = entry.get("id")
        pr_id = entry.get("plan_run_id")
        hid = entry.get("host_id")
        reported_epoch = entry.get("coordinator_epoch", 0)
        if not prh_id or not pr_id or not hid:
            continue
        row = await db.get(_PRH, prh_id)
        if row is None:
            continue
        # Ownership validation: the PlanRunHost row must belong to THIS host
        # AND the requesting agent_instance (Step 5b收口).
        if row.host_id != payload.host_id:
            logger.warning(
                "coord_hb_host_mismatch prh=%d claimed=%s actual=%s",
                prh_id, payload.host_id, row.host_id,
            )
            stale_host_ids.append(prh_id)
            current_epochs[int(prh_id)] = int(row.coordinator_epoch or 0)
            continue
        if row.coordinator_epoch > reported_epoch:
            stale_host_ids.append(prh_id)
            current_epochs[int(prh_id)] = int(row.coordinator_epoch or 0)
            continue
        row.coordinator_epoch = max(row.coordinator_epoch, reported_epoch)
        row.coordinator_heartbeat_at = now
        reported_phase = entry.get("phase")
        if reported_phase in _VALID_COORDINATOR_PHASES:
            row.phase = reported_phase

    await db.commit()
    return ok(_CoordinatorHeartbeatOut(
        accepted=len(stale_host_ids) == 0,
        stale_plan_run_host_ids=stale_host_ids,
        current_coordinator_epochs=current_epochs,
    ))


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
    trace_event_id = (payload.trace_event_id or "").strip()
    if not trace_event_id:
        # 单步 status 端点没有独立批次 id：用载荷内容派生稳定 id，
        # 同一逻辑迁移（如 RUNNING→FAILED）各自幂等，重试不重复插入。
        metadata_json = (
            json.dumps(payload.metadata, sort_keys=True, default=str)
            if payload.metadata else ""
        )
        digest = hashlib.sha256(
            (
                f"{job_id}\0{step_id}\0{payload.status}\0"
                f"{payload.started_at or ''}\0{payload.exit_code or ''}\0"
                f"{payload.error_message or ''}\0{metadata_json}"
            ).encode("utf-8")
        ).hexdigest()[:24]
        trace_event_id = f"status:{digest}"
    trace = {
        "job_id": job_id,
        "step_id": step_id,
        "stage": "execute",
        "event_type": "status_update",
        "status": payload.status,
        "exit_code": payload.exit_code,
        "metadata": payload.metadata,
        "error_message": payload.error_message,
        "trace_event_id": trace_event_id,
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
                "SUCCESS", "PARTIAL_SUCCESS", "FAILED",
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
            raise HTTPException(status_code=400, detail=f"invalid next_retry_at: {payload.next_retry_at}") from None

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
    部分接受（#1048）：单条**永久**不可恢复（契约违规 / job 不存在 / 租约
    fencing 不匹配 / detected_at 非法）只隔离该条并在响应 ``rejected`` 里逐条
    报告，不再整批 404/400 连坐 —— 否则 50 条批次混入一条坏记录，其余正常信号
    会被 Agent 侧反复重试直至全部进死信。暂时性失败（DB 不可用等）仍整批失败。
    """
    return ok(await ingest_agent_log_signals(db, payload))


@router.post("/device-log-events", response_model=ApiResponse[dict])
async def ingest_device_log_events(
    payload: DeviceLogEventBatchIn,
    db: AsyncSession = Depends(get_async_db),
    _=Depends(_verify_agent),
):
    """批量创建或更新 DeviceLogEvent（ADR-0028 D1）。

    提供 ``id`` 时按主键更新 ``state`` / ``remote_path`` / ``checksum`` 等字段；
    未提供 ``id`` 时插入新行。可选 ``link_signal_seq_no`` 将对应
    ``(job_id, seq_no)`` 的 ``job_log_signal.device_log_event_id`` 关联到本事件。
    """
    return ok(await ingest_agent_device_log_events(db, payload))


@router.get("/device-log-events", response_model=ApiResponse[dict])
async def list_device_log_events(
    host_id: str,
    state: Optional[str] = None,
    limit: Optional[int] = None,
    db: AsyncSession = Depends(get_async_db),
    _=Depends(_verify_agent),
):
    """Agent 重启恢复：按 host + 可选 state 拉取待处理事件（``detected_at`` 升序）。"""
    return ok(await list_agent_device_log_events(
        db, host_id=host_id, state=state, limit=limit,
    ))


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
                f"(STP_AEE_NFS_ROOT): {exc}"
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

    ADR-0038 D5bis（#1805 矩阵 row 12）：recognition/recovery 是**第三条**能让在飞
    作业回到退役主机的路径。D5bis 要求派发/认领**活读 `retired_at`**、在飞 Run 以
    显式 `HOST_RETIRED` 收敛——若本端点仍对退役主机返回 `RESUME`，作业就会绕过
    那条收敛被复活（实测修复前确为 `RESUME/same_boot_instance_takeover`）。
    故退役主机**不 RESUME**，改为让 Agent 本地停止（`ABORT_LOCAL`），与「退役即
    不再使用该机」一致；作业的终态收敛仍由既有 abort/lease 回收链完成。

    退役机的 `pending_outbox` 照常推导（#2030）：终态证据必须被 ack/上传，
    否则 Agent 每轮重发且永不被 ack——回收类数据在 D5 下允许触达退役机。
    """
    return ok(await sync_agent_recovery(db, payload))


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


# ── 升级门禁（#1249）：Ansible 等外部升级入口复用 ADR-0021 D7/D8 协议 ──────────
# 鉴权沿用 agent secret（Ansible 侧已有 AGENT_SECRET）；窗口由调用方在升级完成后
# release，异常路径靠 maintenance_until TTL 过期兜底（与 UI 热更新同一实现）。


class UpgradeGateRequest(BaseModel):
    holder: str = ""
    abort_running_jobs: bool = False


class UpgradeGateReleaseRequest(BaseModel):
    holder: str = ""


def _raise_upgrade_gate_http(host_id: str, exc: Exception) -> None:
    if isinstance(exc, HostNotFoundError):
        raise HTTPException(
            status_code=404,
            detail={"code": "HOST_NOT_FOUND", "message": str(exc)},
        ) from None
    if isinstance(exc, HostAbortPendingError):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "HOST_ABORT_PENDING",
                "message": (
                    f"Abort is still draining for {len(exc.active_jobs)} job(s) "
                    f"on host {host_id}. Retry in approximately "
                    f"{exc.retry_after_seconds}s."
                ),
                "active_jobs": exc.active_jobs,
                "retry_after_seconds": exc.retry_after_seconds,
            },
        ) from None
    if isinstance(exc, HostHasActiveJobsError):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "HOST_HAS_ACTIVE_JOBS",
                "message": (
                    f"Host {host_id} has {len(exc.active_jobs)} active job(s). "
                    "Retry with abort_running_jobs=true to abort then upgrade."
                ),
                "active_jobs": exc.active_jobs,
            },
        ) from None
    if isinstance(exc, HostRetiredError):
        # ADR-0038 D5：退役主机拒绝执行/配置类动作（升级门禁同族）
        raise HTTPException(
            status_code=409,
            detail={
                "code": "HOST_RETIRED",
                "message": (
                    f"Host {host_id} is retired; unretire it before upgrade "
                    "(ADR-0038 D5)."
                ),
            },
        ) from None
    if isinstance(exc, HostAbortDrainTimeoutError):
        raise HTTPException(
            status_code=504,
            detail={
                "code": "ABORT_DRAIN_TIMEOUT",
                "message": (
                    f"Aborted jobs but {len(exc.lingering_jobs)} job(s) on host "
                    f"{host_id} did not reach a terminal state in time. "
                    "Investigate the agent or retry."
                ),
                "lingering_jobs": exc.lingering_jobs,
                "abort_summary": exc.abort_summary,
            },
        ) from None
    if isinstance(exc, HostMaintenanceConflict):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "HOST_IN_MAINTENANCE",
                "message": (
                    f"Host {host_id} is already in a maintenance window "
                    "(another upgrade is in progress). Retry later."
                ),
            },
        ) from None
    raise exc


@router.post("/hosts/{host_id}/upgrade-gate")
def acquire_upgrade_gate(
    host_id: str,
    payload: UpgradeGateRequest,
    db: Session = Depends(get_db),
    _=Depends(_verify_agent),
):
    """外部升级入口申请维护窗口（#1249，agent secret 鉴权）。

    成功即持有窗口：期间该主机的派发与 claim 都被跳过（#960 互斥语义）。
    调用方必须在升级完成后调用 ``.../upgrade-gate/release``；进程崩溃等
    异常路径由维护窗口 TTL 过期兜底。
    """
    effective_holder = payload.holder.strip() or f"upgrade-gate:{secrets.token_hex(4)}"
    try:
        gate = begin_host_upgrade(
            db,
            host_id,
            holder=effective_holder,
            abort_running_jobs=payload.abort_running_jobs,
            triggered_by="agent-api",
        )
    except (
        HostNotFoundError,
        HostAbortPendingError,
        HostHasActiveJobsError,
        HostAbortDrainTimeoutError,
        HostMaintenanceConflict,
    ) as exc:
        _raise_upgrade_gate_http(host_id, exc)

    record_audit(
        db,
        action="upgrade_gate_acquire",
        resource_type="host",
        resource_id=host_id,
        details={
            "holder": effective_holder,
            "abort_running_jobs": payload.abort_running_jobs,
            "active_before": len(gate["active_jobs"]),
            "aborted_jobs": (gate["aborted_summary"] or {}).get("aborted_jobs", []),
        },
        username="agent-api",
    )
    db.commit()
    return ok(gate)


@router.post("/hosts/{host_id}/upgrade-gate/release")
def release_upgrade_gate(
    host_id: str,
    payload: UpgradeGateReleaseRequest,
    db: Session = Depends(get_db),
    _=Depends(_verify_agent),
):
    """释放维护窗口；holder 不匹配时不误清他人窗口（幂等，可重复调用）。"""
    holder = payload.holder.strip()
    if not holder:
        raise_api_http_error(400, "HOLDER_REQUIRED", "holder must not be empty")
    end_host_upgrade(db, host_id, holder)
    record_audit(
        db,
        action="upgrade_gate_release",
        resource_type="host",
        resource_id=host_id,
        details={"holder": holder},
        username="agent-api",
    )
    db.commit()
    return ok({"host_id": host_id, "holder": holder, "released": True})
