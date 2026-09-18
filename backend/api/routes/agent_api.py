"""Agent API: job claim, status update, step trace upload, heartbeat.

Authentication: X-Agent-Secret via ``auth.verify_agent_secret``（本模块别名
``_verify_agent``）。私有符号请从对应 ``backend.services.agent_*`` 导入，
不再经本路由 re-export。
"""

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from backend.api.response import ApiResponse, ok
from backend.core.database import get_async_db, get_db
from backend.api.routes.auth import get_current_active_user
from backend.api.routes.auth import verify_agent_secret as _verify_agent
from backend.services.agent_recovery import (
    _RecoverySyncIn,
    sync_agent_recovery,
)
from backend.services.agent_lease_extend import (
    _ExtendBatchIn,
    _ExtendBatchOut,
    extend_agent_leases_batch,
)
from backend.services.agent_claim import (
    ClaimRequest,
    JobOut,
    claim_agent_jobs,
)
from backend.services.agent_device_log_events import (
    DeviceLogEventBatchIn,
    ingest_agent_device_log_events,
    list_agent_device_log_events,
)
from backend.services.agent_log_signals import (
    LogSignalBatchIn,
    ingest_agent_log_signals,
)
from backend.services.agent_patrol_heartbeat import (
    PatrolHeartbeatIn,
    PatrolHeartbeatOut,
    record_agent_patrol_heartbeat,
)
from backend.services.agent_coordinator_heartbeat import (
    _CoordinatorHeartbeatIn,
    _CoordinatorHeartbeatOut,
    record_agent_coordinator_heartbeat,
)
from backend.services.agent_artifacts import (
    ArtifactIn,
    ArtifactOut,
    ingest_agent_artifact,
)
from backend.services.agent_host_heartbeat import (
    HeartbeatRequest,
    HeartbeatResponse,
    record_agent_host_heartbeat,
)
from backend.services.agent_upgrade_gate import (
    UpgradeGateRequest,
    UpgradeGateReleaseRequest,
    acquire_agent_upgrade_gate,
    release_agent_upgrade_gate,
)
from backend.services.agent_job_heartbeat import (
    _ExtendLockIn,
    _JobHeartbeatIn,
    extend_agent_job_lock,
    record_agent_job_heartbeat,
)
from backend.services.agent_step_status import (
    StepTraceIn,
    _StepStatusIn,
    update_agent_job_step_status,
    upload_agent_step_traces,
)
from backend.services.agent_job_status import (
    JobStatusUpdate,
    update_agent_job_status,
)
from backend.services.agent_archive_status import get_agent_archive_status
from backend.services.agent_completion import (
    _RunCompleteIn,
    complete_agent_job,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/agent", tags=["agent"])

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
    return ok(await update_agent_job_status(db, job_id, payload))


@router.post("/steps", response_model=ApiResponse[dict])
async def upload_step_traces(
    traces: List[StepTraceIn],
    db: AsyncSession = Depends(get_async_db),
    _=Depends(_verify_agent),
):
    """Batch idempotent StepTrace upsert (Agent replay on reconnect)."""
    return ok(await upload_agent_step_traces(db, traces))


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
    return ok(await record_agent_host_heartbeat(db, payload))


@router.post("/jobs/{job_id}/heartbeat", response_model=ApiResponse[dict])
async def job_heartbeat(
    job_id: int,
    payload: _JobHeartbeatIn,
    db: AsyncSession = Depends(get_async_db),
    _=Depends(_verify_agent),
):
    """Keep an already claimed RUNNING job alive."""
    return ok(await record_agent_job_heartbeat(db, job_id, payload))


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
    return ok(await extend_agent_job_lock(db, job_id, payload))
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
    return ok(await record_agent_coordinator_heartbeat(db, payload))


@router.post("/jobs/{job_id}/steps/{step_id}/status", response_model=ApiResponse[dict])
async def update_job_step_status(
    job_id: int,
    step_id: str,
    payload: _StepStatusIn,
    db: AsyncSession = Depends(get_async_db),
    _=Depends(_verify_agent),
):
    """Update a single step status — upserted as StepTrace."""
    return ok(await update_agent_job_step_status(db, job_id, step_id, payload))


# ── ADR-0022: Patrol Heartbeat ────────────────────────────────────────────────


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
    return ok(await record_agent_patrol_heartbeat(db, job_id, payload))




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


# ── Artifact ingestion（ADR-0018 5B2）────────────────────────────────────────


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
    return ok(await ingest_agent_artifact(db, job_id, payload))


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
    return ok(await get_agent_archive_status(db, host_id))


# ── 升级门禁（#1249）：Ansible 等外部升级入口复用 ADR-0021 D7/D8 协议 ──────────


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
    return ok(acquire_agent_upgrade_gate(db, host_id, payload))


@router.post("/hosts/{host_id}/upgrade-gate/release")
def release_upgrade_gate(
    host_id: str,
    payload: UpgradeGateReleaseRequest,
    db: Session = Depends(get_db),
    _=Depends(_verify_agent),
):
    """释放维护窗口；holder 不匹配时不误清他人窗口（幂等，可重复调用）。"""
    return ok(release_agent_upgrade_gate(db, host_id, payload))
