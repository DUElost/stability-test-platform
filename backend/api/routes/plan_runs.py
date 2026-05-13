"""PlanRun API — ADR-0020.

Provides PlanRun list/detail/jobs/summary endpoints.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional
from urllib.parse import unquote, urlparse

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.api.response import ApiResponse, ok
from backend.api.routes.auth import get_current_active_user, User
from backend.core.audit import record_audit
from backend.core.database import get_db
from backend.core.metrics import record_patrol_manual_action
from backend.models.audit import AuditLog
from backend.models.enums import JobStatus, PlanRunStatus
from backend.models.host import Device
from backend.models.job import JobArtifact, JobInstance, JobLogSignal, StepTrace
from backend.models.plan import Plan, PlanStep
from backend.models.plan_run import PlanRun
from backend.services.plan_run_abort import (
    PlanRunAbortError,
    abort_plan_run,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["plan-runs"])


# ── Schemas ──────────────────────────────────────────────────────────────

class StepTraceOut(BaseModel):
    id: int
    job_id: int
    step_id: str
    stage: str
    event_type: str
    status: str
    output: Optional[str] = None
    error_message: Optional[str] = None
    original_ts: str
    created_at: str

    class Config:
        from_attributes = True


class JobInstanceOut(BaseModel):
    id: int
    plan_run_id: Optional[int] = None
    plan_id: Optional[int] = None
    device_id: int
    device_serial: Optional[str] = None
    host_id: Optional[str] = None
    status: str
    status_reason: Optional[str] = None
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    created_at: Optional[str] = None
    step_traces: list[StepTraceOut] = []

    class Config:
        from_attributes = True


class PlanRunOut(BaseModel):
    id: int
    plan_id: int
    status: str
    failure_threshold: float
    run_type: str
    triggered_by: Optional[str] = None
    started_at: str
    ended_at: Optional[str] = None
    result_summary: Optional[dict] = None
    # ADR-0021: dispatch gate progress lives under run_context.precheck.
    run_context: Optional[dict] = None
    plan_snapshot: Optional[dict] = None
    parent_plan_run_id: Optional[int] = None
    root_plan_run_id: Optional[int] = None
    chain_index: int = 0
    next_plan_triggered: bool = False
    jobs: list[JobInstanceOut] = []

    class Config:
        from_attributes = True


# ── Helpers ──────────────────────────────────────────────────────────────

def _iso(v) -> str | None:
    if v is None:
        return None
    return v.isoformat()


def _plan_run_out(pr: PlanRun, jobs: list[JobInstanceOut] | None = None) -> PlanRunOut:
    return PlanRunOut(
        id=pr.id,
        plan_id=pr.plan_id,
        status=pr.status,
        failure_threshold=pr.failure_threshold,
        run_type=pr.run_type,
        triggered_by=pr.triggered_by,
        started_at=_iso(pr.started_at) or "",
        ended_at=_iso(pr.ended_at),
        result_summary=pr.result_summary,
        run_context=pr.run_context,
        plan_snapshot=pr.plan_snapshot,
        parent_plan_run_id=pr.parent_plan_run_id,
        root_plan_run_id=pr.root_plan_run_id,
        chain_index=pr.chain_index or 0,
        next_plan_triggered=bool(pr.next_plan_triggered),
        jobs=jobs or [],
    )


def _step_out(t: StepTrace) -> StepTraceOut:
    return StepTraceOut(
        id=t.id, job_id=t.job_id, step_id=t.step_id, stage=t.stage,
        event_type=t.event_type, status=t.status, output=t.output,
        error_message=t.error_message,
        original_ts=_iso(t.original_ts) or "",
        created_at=_iso(t.created_at) or "",
    )


def _job_out(job: JobInstance, traces: list, device_serial: str | None = None) -> JobInstanceOut:
    return JobInstanceOut(
        id=job.id, plan_run_id=job.plan_run_id, plan_id=job.plan_id,
        device_id=job.device_id, device_serial=device_serial,
        host_id=job.host_id, status=job.status,
        status_reason=job.status_reason,
        started_at=_iso(job.started_at),
        ended_at=_iso(job.ended_at),
        created_at=_iso(job.created_at),
        step_traces=[_step_out(t) for t in traces],
    )


# ── Endpoints ────────────────────────────────────────────────────────────

@router.get("/plan-runs", response_model=ApiResponse[list[PlanRunOut]])
def list_plan_runs(
    skip: int = 0,
    limit: int = 50,
    plan_id: Optional[int] = None,
    status: Optional[str] = None,
    db: Session = Depends(get_db),
):
    q = select(PlanRun).order_by(PlanRun.started_at.desc())
    if plan_id is not None:
        q = q.where(PlanRun.plan_id == plan_id)
    if status is not None:
        q = q.where(PlanRun.status == status.upper())
    runs = db.execute(q.offset(skip).limit(limit)).scalars().all()
    return ok([_plan_run_out(r) for r in runs])


@router.get("/plan-runs/{run_id}", response_model=ApiResponse[PlanRunOut])
def get_plan_run(run_id: int, db: Session = Depends(get_db)):
    pr = db.get(PlanRun, run_id)
    if pr is None:
        raise HTTPException(status_code=404, detail="plan run not found")
    jobs = db.execute(
        select(JobInstance).where(JobInstance.plan_run_id == run_id)
    ).scalars().all()
    return ok(_plan_run_out(pr, jobs=[_job_out(j, []) for j in jobs]))


@router.get("/plan-runs/{run_id}/jobs", response_model=ApiResponse[list[JobInstanceOut]])
def list_plan_run_jobs(run_id: int, db: Session = Depends(get_db)):
    jobs = db.execute(
        select(JobInstance).where(JobInstance.plan_run_id == run_id)
    ).scalars().all()
    if not jobs:
        return ok([])

    device_ids = list({j.device_id for j in jobs})
    devices: dict[int, str] = {}
    if device_ids:
        rows = db.execute(
            select(Device.id, Device.serial).where(Device.id.in_(device_ids))
        ).all()
        devices = {r.id: r.serial for r in rows}

    job_ids = [j.id for j in jobs]
    all_traces = db.execute(
        select(StepTrace)
        .where(StepTrace.job_id.in_(job_ids))
        .order_by(StepTrace.original_ts)
    ).scalars().all()
    traces_by_job: dict[int, list] = {}
    for t in all_traces:
        traces_by_job.setdefault(t.job_id, []).append(t)

    return ok([
        _job_out(j, traces_by_job.get(j.id, []), devices.get(j.device_id))
        for j in jobs
    ])


# ── Abort ────────────────────────────────────────────────────────────────


class PlanRunAbortIn(BaseModel):
    reason: Optional[str] = None


@router.post(
    "/plan-runs/{run_id}/abort", response_model=ApiResponse[dict]
)
def abort_plan_run_endpoint(
    run_id: int,
    payload: Optional[PlanRunAbortIn] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """ADR-0021 D7 — abort a PlanRun.

    Returns 409 if the PlanRun is already terminal.  Otherwise releases
    active leases / marks PENDING jobs ABORTED / closes the run, then
    returns immediately (Agent drain happens asynchronously).
    """
    reason = (payload.reason if payload else None) or "aborted_by_user"
    try:
        summary = abort_plan_run(
            run_id,
            db=db,
            reason=reason,
            triggered_by=current_user.username if current_user else "api",
            audit_user_id=current_user.id if current_user else None,
            audit_username=current_user.username if current_user else None,
        )
    except PlanRunAbortError as exc:
        msg = str(exc)
        if "not found" in msg:
            raise HTTPException(status_code=404, detail=msg)
        raise HTTPException(status_code=409, detail=msg)
    return ok(summary)


# ── ADR-0022: Manual retry / exit for patrol-backoff jobs ───────────────────


_NON_TERMINAL_JOB_STATUSES = {
    JobStatus.PENDING.value,
    JobStatus.RUNNING.value,
}


class JobManualActionIn(BaseModel):
    reason: Optional[str] = None


class JobManualActionOut(BaseModel):
    job_id: int
    plan_run_id: int
    action: str          # 'manual_retry' | 'manual_exit'
    status: str          # job status after the action
    manual_action: Optional[str] = None
    next_retry_at: Optional[str] = None
    current_failure_streak: int = 0


def _load_job_in_run(db: Session, run_id: int, job_id: int) -> JobInstance:
    job = db.get(JobInstance, job_id)
    if job is None or job.plan_run_id != run_id:
        raise HTTPException(status_code=404, detail="job not found in this plan run")
    return job


def _emit_job_status_invalidation(
    run_id: int, job_id: int, status: str, reason: str
) -> None:
    """ADR-0021 C5c: notify the frontend that a job's row needs a refetch.

    Used by the sync manual-retry / manual-exit endpoints.  We deliberately
    use ``schedule_emit`` (thread-safe bridge) because these handlers run on
    sync sessions and must not await.  The payload mirrors the agent-emitted
    ``job_status`` event so the frontend's existing handler can reuse it as
    a pure invalidation hint — no DB state is conveyed in the payload.
    """
    try:
        from backend.realtime.socketio_server import schedule_emit
    except Exception:
        return
    try:
        schedule_emit(
            "job_status",
            {
                "type": "JOB_STATUS",
                "payload": {
                    "job_id": int(job_id),
                    "status": status,
                    "reason": reason,
                },
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
            namespace="/dashboard",
            room=f"plan_run:{run_id}",
        )
    except Exception:
        logger.debug("emit_job_status_invalidation_failed", exc_info=True)


@router.post(
    "/plan-runs/{run_id}/jobs/{job_id}/manual-retry",
    response_model=ApiResponse[JobManualActionOut],
)
def manual_retry_job(
    run_id: int,
    job_id: int,
    payload: Optional[JobManualActionIn] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """ADR-0022 D7: clear backoff and force the next patrol cycle to run now.

    Sets ``next_retry_at = now()`` and ``manual_action = 'RETRY_NOW'`` so the
    Agent picks it up on the next heartbeat.  **Does not reset**
    ``current_failure_streak`` — diagnostic information is preserved.
    """
    job = _load_job_in_run(db, run_id, job_id)
    if job.status not in _NON_TERMINAL_JOB_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"job is in terminal status {job.status}; cannot retry",
        )

    reason = (payload.reason if payload else None) or "manual_retry"
    now = datetime.now(timezone.utc)

    job.next_retry_at = now
    job.manual_action = "RETRY_NOW"
    job.updated_at = now
    db.flush()

    record_audit(
        db,
        action="patrol_manual_retry",
        resource_type="job_instance",
        resource_id=job_id,
        details={
            "plan_run_id": run_id,
            "reason": reason,
            "current_failure_streak": job.current_failure_streak or 0,
            "triggered_by": current_user.username if current_user else None,
        },
        user_id=current_user.id if current_user else None,
        username=current_user.username if current_user else None,
    )
    db.commit()
    db.refresh(job)

    logger.info(
        "patrol_manual_retry plan_run=%d job=%d streak=%d",
        run_id, job_id, job.current_failure_streak or 0,
    )
    record_patrol_manual_action("manual_retry")
    _emit_job_status_invalidation(run_id, job_id, job.status, "manual_retry")

    return ok(JobManualActionOut(
        job_id=job_id,
        plan_run_id=run_id,
        action="manual_retry",
        status=job.status,
        manual_action=job.manual_action,
        next_retry_at=_iso(job.next_retry_at),
        current_failure_streak=job.current_failure_streak or 0,
    ))


@router.post(
    "/plan-runs/{run_id}/jobs/{job_id}/manual-exit",
    response_model=ApiResponse[JobManualActionOut],
)
def manual_exit_job(
    run_id: int,
    job_id: int,
    payload: Optional[JobManualActionIn] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """ADR-0022 D7: request that the Agent skip the rest of patrol and abort.

    Sets ``manual_action = 'EXIT_REQUESTED'``.  The Agent observes this on the
    next heartbeat and exits the patrol loop **without running teardown** (BO4).
    Recycler / device lease release ensures the device returns to the pool.

    The job's status remains its current (PENDING/RUNNING) value here; it
    transitions to ABORTED once the Agent reports the terminal state via
    /jobs/{id}/complete (or via Recycler's stall detection).
    """
    job = _load_job_in_run(db, run_id, job_id)
    if job.status not in _NON_TERMINAL_JOB_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"job is in terminal status {job.status}; cannot exit",
        )

    reason = (payload.reason if payload else None) or "manual_exit"
    now = datetime.now(timezone.utc)

    job.manual_action = "EXIT_REQUESTED"
    if not job.status_reason:
        job.status_reason = f"patrol_manual_exit_pending: {reason}"
    job.updated_at = now
    db.flush()

    record_audit(
        db,
        action="patrol_manual_exit",
        resource_type="job_instance",
        resource_id=job_id,
        details={
            "plan_run_id": run_id,
            "reason": reason,
            "current_failure_streak": job.current_failure_streak or 0,
            "triggered_by": current_user.username if current_user else None,
        },
        user_id=current_user.id if current_user else None,
        username=current_user.username if current_user else None,
    )
    db.commit()
    db.refresh(job)

    logger.info(
        "patrol_manual_exit plan_run=%d job=%d streak=%d",
        run_id, job_id, job.current_failure_streak or 0,
    )
    record_patrol_manual_action("manual_exit")
    _emit_job_status_invalidation(run_id, job_id, job.status, "manual_exit_pending")

    return ok(JobManualActionOut(
        job_id=job_id,
        plan_run_id=run_id,
        action="manual_exit",
        status=job.status,
        manual_action=job.manual_action,
        next_retry_at=_iso(job.next_retry_at),
        current_failure_streak=job.current_failure_streak or 0,
    ))


# ── ADR-0021/ADR-0022 C5a₂: PlanRunDetailPage 聚合端点 ──────────────────
#
# 5 个独立 GET 端点供前端分别拉取,所有返回值都是 PlanRun 范围内的聚合视图;
# 注意:
#   - 这些端点是 RUNNING / 终态都可调用的(终态后值定格,前端可缓存)
#   - chain 端点会沿 parent_plan_run_id 链向 root 回溯;next 节点是 Plan.next_plan_id
#     指向的 Plan,是否已触发由 PlanRun.next_plan_triggered 决定
#   - timeline 端点的 step_trace 聚合仅返回 init / patrol / teardown 三阶段的
#     succeeded/failed 计数;ADR-0022 后 patrol 成功步骤不再写 step_trace,
#     真实 patrol 进度从 JobInstance.patrol_*_cycle_count 派生
#   - events 端点融合 4 个数据源:
#     1) step_trace(失败步骤,作为 init/patrol/teardown 阶段事件)
#     2) job_log_signal(watcher 异常,作为 patrol 阶段事件)
#     3) audit_logs(plan_run / job_instance / dispatch_gate,作为 system 事件)
#     4) PlanRun 自身 trigger 事件 + patrol heartbeat 周期摘要
#   - devices 端点的 ui_status 派生规则:
#       COMPLETED                            → completed
#       FAILED / ABORTED / UNKNOWN           → failed
#       PENDING                              → pending
#       RUNNING + manual_action=EXIT_REQ.    → backoff
#       RUNNING + next_retry_at > now        → backoff
#       RUNNING + log_signal_count > 0       → risk
#       RUNNING (其他)                        → running
#   - watcher-summary 默认 60min 窗口,与上一窗口对比得到 trend
#
# 性能保障:依赖 ADR-0022 patrol 心跳聚合 + ADR-0021 C5a₂ 新建的两个
# step_trace 复合索引 (idx_step_trace_job_stage / idx_step_trace_job_status_ts)。

# ── 公共常量 ─────────────────────────────────────────────────────────────

_LIVE_PATROL_HEARTBEAT_WINDOW = timedelta(seconds=180)  # heartbeat 视为活跃的窗口
_DEFAULT_WATCHER_WINDOW_MIN   = 60
_MAX_WATCHER_WINDOW_MIN       = 1440  # 1 天
_MAX_EVENTS_LIMIT             = 500
_DEFAULT_EVENTS_LIMIT         = 100

_FAILED_JOB_STATUSES   = {JobStatus.FAILED.value, JobStatus.ABORTED.value, JobStatus.UNKNOWN.value}
_TERMINAL_PR_STATUSES  = {
    PlanRunStatus.SUCCESS.value,
    PlanRunStatus.PARTIAL_SUCCESS.value,
    PlanRunStatus.FAILED.value,
    PlanRunStatus.DEGRADED.value,
}


def _require_plan_run(db: Session, run_id: int) -> PlanRun:
    pr = db.get(PlanRun, run_id)
    if pr is None:
        raise HTTPException(status_code=404, detail="plan run not found")
    return pr


def _duration_seconds(start, end) -> float | None:
    if start is None:
        return None
    if end is None:
        end = datetime.now(timezone.utc)
    try:
        return max(0.0, (_aware(end) - _aware(start)).total_seconds())
    except TypeError:
        return None


def _aware(ts: datetime | None) -> datetime | None:
    """Normalise naive datetimes to UTC.

    SQLite (used in test mode) does not store tz info; PostgreSQL does.
    Several aggregation paths compare DB-stored values against
    ``datetime.now(timezone.utc)`` and would otherwise raise
    ``TypeError: can't compare offset-naive and offset-aware datetimes``.
    """
    if ts is None:
        return None
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts


# ── Endpoint 1: GET /plan-runs/{id}/chain ────────────────────────────────

class ChainNodeOut(BaseModel):
    plan_id: int
    plan_name: Optional[str] = None
    plan_run_id: Optional[int] = None
    status: str                        # PlanRun.status 或 'pending'(尚未触发)
    chain_index: int
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    duration_seconds: Optional[float] = None
    failure_threshold: float
    pass_rate: Optional[float] = None  # 来自 PlanRun.result_summary
    is_current: bool = False
    is_blocked: bool = False
    block_reason: Optional[str] = None


class PlanChainOut(BaseModel):
    plan_run_id: int
    root_plan_run_id: int
    nodes: list[ChainNodeOut]


def _chain_node_from_run(pr: PlanRun, plan_name: Optional[str], is_current: bool) -> ChainNodeOut:
    summary = pr.result_summary or {}
    pass_rate = summary.get("pass_rate") if isinstance(summary, dict) else None
    return ChainNodeOut(
        plan_id=pr.plan_id,
        plan_name=plan_name,
        plan_run_id=pr.id,
        status=pr.status,
        chain_index=pr.chain_index or 0,
        started_at=_iso(pr.started_at),
        ended_at=_iso(pr.ended_at),
        duration_seconds=_duration_seconds(pr.started_at, pr.ended_at),
        failure_threshold=pr.failure_threshold,
        pass_rate=pass_rate,
        is_current=is_current,
    )


@router.get("/plan-runs/{run_id}/chain", response_model=ApiResponse[PlanChainOut])
def get_plan_run_chain(run_id: int, db: Session = Depends(get_db)):
    """ADR-0020 §6: PlanRun chain 上下文 — 沿 parent_plan_run_id 回溯到 root,
    再沿 next_plan_id + next_plan_triggered 向前查找下一段。

    返回的 nodes 列表按 chain_index 升序,包含:
      - 0..N 个 parent PlanRun (已触发)
      - 1 个 current PlanRun(is_current=True)
      - 0..1 个未触发的 next Plan 节点(plan_run_id=None, status='pending')
    """
    pr = _require_plan_run(db, run_id)

    # 1) 沿 chain 收集所有已存在的 PlanRun
    root_id = pr.root_plan_run_id or pr.id
    chain_runs = db.execute(
        select(PlanRun)
        .where((PlanRun.id == root_id) | (PlanRun.root_plan_run_id == root_id))
        .order_by(PlanRun.chain_index.asc(), PlanRun.id.asc())
    ).scalars().all()

    # 2) 批量取 plan name
    plan_ids = list({r.plan_id for r in chain_runs})
    plan_names: dict[int, str] = {}
    if plan_ids:
        rows = db.execute(
            select(Plan.id, Plan.name).where(Plan.id.in_(plan_ids))
        ).all()
        plan_names = {r.id: r.name for r in rows}

    nodes = [
        _chain_node_from_run(r, plan_names.get(r.plan_id), is_current=(r.id == pr.id))
        for r in chain_runs
    ]

    # 3) 候选 next Plan(链尾):取 chain_runs 末尾的 PlanRun,看其对应 Plan.next_plan_id
    if chain_runs:
        tail = chain_runs[-1]
        tail_plan = db.get(Plan, tail.plan_id)
        if tail_plan and tail_plan.next_plan_id and not tail.next_plan_triggered:
            next_plan = db.get(Plan, tail_plan.next_plan_id)
            if next_plan is not None:
                # 推断 block 原因
                blocked = False
                reason = None
                summary = tail.result_summary or {}
                if tail.status == PlanRunStatus.RUNNING.value:
                    blocked = True
                    reason = "parent PlanRun 仍在运行,需等待终态"
                elif tail.status not in (PlanRunStatus.SUCCESS.value, PlanRunStatus.PARTIAL_SUCCESS.value):
                    blocked = True
                    pr_summary_failed = summary.get("failed", 0) if isinstance(summary, dict) else 0
                    pr_summary_total  = summary.get("total", 0)  if isinstance(summary, dict) else 0
                    if pr_summary_total:
                        rate = pr_summary_failed / pr_summary_total
                        reason = (
                            f"failure_rate {rate:.1%} > threshold "
                            f"{tail.failure_threshold:.1%}; chain 终止"
                        )
                    else:
                        reason = f"parent status={tail.status}; chain 不触发"

                nodes.append(ChainNodeOut(
                    plan_id=next_plan.id,
                    plan_name=next_plan.name,
                    plan_run_id=None,
                    status="pending",
                    chain_index=(tail.chain_index or 0) + 1,
                    failure_threshold=next_plan.failure_threshold,
                    is_blocked=blocked,
                    block_reason=reason,
                ))

    return ok(PlanChainOut(
        plan_run_id=pr.id,
        root_plan_run_id=root_id,
        nodes=nodes,
    ))


# ── Endpoint 2: GET /plan-runs/{id}/timeline ─────────────────────────────

class StageStepOut(BaseModel):
    step_key: str
    script_name: str
    stage: str
    sort_order: int
    device_total: int                  # = PlanRun jobs 总数
    device_succeeded: int              # event_type=COMPLETED + status=COMPLETED
    device_failed: int                 # event_type=FAILED or status=FAILED
    device_skipped: int = 0            # v3: event_type=COMPLETED + status=SKIPPED
    device_running: int                # = max(0, total - succeeded - failed - skipped)


class StageOut(BaseModel):
    stage: str                         # init / patrol / teardown
    status: str                        # pending / running / completed / failed / skipped
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    duration_seconds: Optional[float] = None
    device_total: int
    device_succeeded: int = 0
    device_failed: int = 0
    device_skipped: int = 0            # v3: summed from steps
    # patrol 专属(从 JobInstance.patrol_*_cycle_count 聚合)
    patrol_cycle_index: Optional[int] = None
    patrol_active_devices: Optional[int] = None
    patrol_interval_seconds: Optional[int] = None
    steps: list[StageStepOut] = []


class PlanRunTimelineOut(BaseModel):
    plan_run_id: int
    current_stage: str                 # init / patrol / teardown / done / pending
    stages: list[StageOut]
    aborted_job_count: int = 0         # v3: ABORTED jobs 计数 (顶层 banner 用)
    triggered_at: str
    triggered_by: Optional[str] = None
    run_type: str
    plan_name: Optional[str] = None


def _stage_status_from_steps(
    stage: str,
    *,
    pr_status: str,
    job_total: int,
    succeeded: int,
    failed: int,
    has_running_jobs: bool,
) -> str:
    """从 step_trace 聚合派生 stage 整体状态。

    init 阶段:任意 step 失败 → failed;全部 step ok 且 patrol 已启动 → completed;否则 running/pending
    teardown 阶段:终态 PlanRun 时若有 step_trace → completed; 否则 pending/skipped(manual_exit)
    patrol 阶段:running 时 → running; 终态时 → completed (或 failed)
    """
    if pr_status in _TERMINAL_PR_STATUSES:
        if stage == "teardown":
            if succeeded == 0 and failed == 0:
                return "skipped"
            return "completed" if failed == 0 else "failed"
        return "completed" if failed == 0 else "failed"
    # PlanRun RUNNING
    if stage == "init":
        if failed > 0:
            return "failed"
        if succeeded >= job_total:
            return "completed"
        return "running" if has_running_jobs else "pending"
    if stage == "patrol":
        if succeeded == 0 and failed == 0 and not has_running_jobs:
            return "pending"
        return "running"
    return "pending"  # teardown 在 RUNNING 时永远是 pending


@router.get("/plan-runs/{run_id}/timeline", response_model=ApiResponse[PlanRunTimelineOut])
def get_plan_run_timeline(run_id: int, db: Session = Depends(get_db)):
    """ADR-0021/ADR-0022 C5a₂: 业务流时间线 — 按 stage 聚合 step_trace,
    输出三阶段的 succeeded/failed/running 计数与每个 step 的设备级进度。

    patrol 阶段额外暴露 patrol_cycle_index / active_devices(60s 内 heartbeat),
    数据源是 JobInstance.patrol_*_cycle_count(ADR-0022 心跳聚合,而非 step_trace)。
    """
    pr = _require_plan_run(db, run_id)
    plan = db.get(Plan, pr.plan_id)

    jobs = db.execute(
        select(JobInstance).where(JobInstance.plan_run_id == run_id)
    ).scalars().all()
    job_total = len(jobs)
    job_ids = [j.id for j in jobs]
    has_running = any(j.status == JobStatus.RUNNING.value for j in jobs)

    # 1) 静态 step 定义(从 plan_snapshot 或 PlanStep)
    snapshot_steps: list[dict] = []
    if isinstance(pr.plan_snapshot, dict):
        snapshot_steps = pr.plan_snapshot.get("steps") or []
    if not snapshot_steps and pr.plan_id:
        rows = db.execute(
            select(PlanStep)
            .where(PlanStep.plan_id == pr.plan_id)
            .order_by(PlanStep.stage, PlanStep.sort_order)
        ).scalars().all()
        snapshot_steps = [
            {
                "step_key": s.step_key,
                "script_name": s.script_name,
                "stage": s.stage,
                "sort_order": s.sort_order,
            }
            for s in rows
        ]

    # 2) 聚合 step_trace:按 (stage, step_id, event_type, status) 分桶
    #    v3: 基于真实 (event_type, status) 组合; 排除 STARTED 中间事件
    step_agg: dict[tuple[str, str], dict[str, int]] = {}
    if job_ids:
        agg_rows = db.execute(
            select(
                StepTrace.stage,
                StepTrace.step_id,
                StepTrace.event_type,
                StepTrace.status,
                func.count(StepTrace.id),
            )
            .where(
                StepTrace.job_id.in_(job_ids),
                StepTrace.event_type != "STARTED",
            )
            .group_by(
                StepTrace.stage, StepTrace.step_id,
                StepTrace.event_type, StepTrace.status,
            )
        ).all()
        for stage, step_id, event_type, status, cnt in agg_rows:
            key = (stage, step_id)
            bucket = step_agg.setdefault(
                key, {"succeeded": 0, "failed": 0, "skipped": 0},
            )
            if event_type == "COMPLETED" and status == "COMPLETED":
                bucket["succeeded"] += cnt
            elif event_type == "COMPLETED" and status == "SKIPPED":
                bucket["skipped"] += cnt
            elif event_type == "FAILED" or status == "FAILED":
                bucket["failed"] += cnt
            # event_type=RUN_COMPLETE + step_id=__job__: step_id 不在
            # plan_snapshot, 不会被纳入 stage 桶, 无需特殊处理

    # 3) 按 stage 组织 steps;按 plan_snapshot 顺序保留
    stages_def: dict[str, list[StageStepOut]] = {"init": [], "patrol": [], "teardown": []}
    for s in snapshot_steps:
        stage = s.get("stage")
        if stage not in stages_def:
            continue
        key = (stage, s.get("step_key", ""))
        agg = step_agg.get(key, {"succeeded": 0, "failed": 0, "skipped": 0})
        succeeded = agg["succeeded"]
        failed = agg["failed"]
        skipped = agg["skipped"]
        running = max(0, job_total - succeeded - failed - skipped) if has_running else 0
        stages_def[stage].append(StageStepOut(
            step_key=s.get("step_key", ""),
            script_name=s.get("script_name", ""),
            stage=stage,
            sort_order=int(s.get("sort_order", 0)),
            device_total=job_total,
            device_succeeded=succeeded,
            device_failed=failed,
            device_skipped=skipped,
            device_running=running,
        ))

    # 4) patrol 心跳聚合
    patrol_cycle_index = None
    patrol_active = None
    if jobs:
        cycles = [j.patrol_cycle_count or 0 for j in jobs]
        patrol_cycle_index = max(cycles) if cycles else 0
        live_threshold = datetime.now(timezone.utc) - _LIVE_PATROL_HEARTBEAT_WINDOW
        patrol_active = sum(
            1 for j in jobs
            if j.last_patrol_heartbeat_at
            and _aware(j.last_patrol_heartbeat_at) >= live_threshold
        )

    # 5) 每 stage 的 succeeded/failed/skipped 总数(求和 step 级)
    def _sum_stage(stage_name: str, accessor: str) -> int:
        return sum(getattr(s, accessor) for s in stages_def.get(stage_name, []))

    # 6) 每 stage 的 started_at / ended_at — 取该 stage 第一个/最后一个 step_trace
    stage_ts: dict[str, dict[str, Optional[datetime]]] = {
        "init": {"started_at": None, "ended_at": None},
        "patrol": {"started_at": None, "ended_at": None},
        "teardown": {"started_at": None, "ended_at": None},
    }
    if job_ids:
        ts_rows = db.execute(
            select(
                StepTrace.stage,
                func.min(StepTrace.original_ts),
                func.max(StepTrace.original_ts),
            )
            .where(StepTrace.job_id.in_(job_ids))
            .group_by(StepTrace.stage)
        ).all()
        for stage, ts_min, ts_max in ts_rows:
            if stage in stage_ts:
                stage_ts[stage]["started_at"] = ts_min
                stage_ts[stage]["ended_at"]   = ts_max

    # 7) current_stage 推导
    current_stage = "pending"
    if pr.status in _TERMINAL_PR_STATUSES:
        current_stage = "done"
    elif stage_ts["teardown"]["started_at"]:
        current_stage = "teardown"
    elif stage_ts["patrol"]["started_at"] or patrol_cycle_index:
        current_stage = "patrol"
    elif stage_ts["init"]["started_at"]:
        current_stage = "init"

    stages_out: list[StageOut] = []
    for stage_name in ("init", "patrol", "teardown"):
        steps = stages_def.get(stage_name, [])
        succeeded = _sum_stage(stage_name, "device_succeeded")
        failed    = _sum_stage(stage_name, "device_failed")
        skipped   = _sum_stage(stage_name, "device_skipped")
        st = _stage_status_from_steps(
            stage_name,
            pr_status=pr.status,
            job_total=job_total,
            succeeded=succeeded,
            failed=failed,
            has_running_jobs=has_running,
        )
        s_at = stage_ts[stage_name]["started_at"]
        e_at = stage_ts[stage_name]["ended_at"] if st in {"completed", "failed", "skipped"} else None
        stage_obj = StageOut(
            stage=stage_name,
            status=st,
            started_at=_iso(s_at),
            ended_at=_iso(e_at),
            duration_seconds=_duration_seconds(s_at, e_at),
            device_total=job_total,
            device_succeeded=succeeded,
            device_failed=failed,
            device_skipped=skipped,
            steps=steps,
        )
        if stage_name == "patrol":
            stage_obj.patrol_cycle_index = patrol_cycle_index
            stage_obj.patrol_active_devices = patrol_active
            stage_obj.patrol_interval_seconds = (
                plan.patrol_interval_seconds if plan else None
            )
        stages_out.append(stage_obj)

    aborted_job_count = sum(1 for j in jobs if j.status == JobStatus.ABORTED.value)

    return ok(PlanRunTimelineOut(
        plan_run_id=pr.id,
        current_stage=current_stage,
        stages=stages_out,
        aborted_job_count=aborted_job_count,
        triggered_at=_iso(pr.started_at) or "",
        triggered_by=pr.triggered_by,
        run_type=pr.run_type,
        plan_name=plan.name if plan else None,
    ))


# ── Endpoint 3: GET /plan-runs/{id}/events ───────────────────────────────

class EventOut(BaseModel):
    ts: str
    stage: str                         # init/patrol/teardown/system/trigger
    severity: str                      # ok/info/warn/err
    category: str                      # step / log_signal / audit / system / trigger
    title: str
    description: str = ""
    job_id: Optional[int] = None
    device_id: Optional[int] = None
    device_serial: Optional[str] = None
    ref: Optional[dict] = None         # {type, id} — 用于跳转 step_trace / log_signal


class PlanRunEventsOut(BaseModel):
    plan_run_id: int
    events: list[EventOut]
    total: int                         # 当前过滤条件下的总数(facets 后)
    facets: dict                       # {by_stage: {...}, by_severity: {...}}


def _log_signal_severity(category: str) -> str:
    cat = (category or "").upper()
    if cat in {"AEE", "VENDOR_AEE", "TOMBSTONE"}:
        return "err"
    if cat in {"ANR", "MOBILELOG"}:
        return "warn"
    return "info"


def _log_signal_title(category: str) -> str:
    cat = (category or "").upper()
    return {
        "AEE": "AEE Crash 检测",
        "VENDOR_AEE": "Vendor AEE Crash",
        "ANR": "ANR",
        "TOMBSTONE": "Tombstone",
        "MOBILELOG": "MOBILELOG",
    }.get(cat, cat or "异常事件")


def _min_aware_dt(*values: datetime | None) -> datetime | None:
    present = [_aware(v) for v in values if v is not None]
    return min(present) if present else None


def _max_aware_dt(*values: datetime | None) -> datetime | None:
    present = [_aware(v) for v in values if v is not None]
    return max(present) if present else None


def _job_has_patrol_signal(job: JobInstance) -> bool:
    return (
        (job.patrol_cycle_count or 0) > 0
        or job.last_patrol_heartbeat_at is not None
        or bool(job.current_patrol_step)
    )


def _build_synthetic_stage_events(
    pr: PlanRun,
    jobs: list[JobInstance],
    stage_meta: dict[str, dict[str, object]],
) -> list[EventOut]:
    if not jobs:
        return []

    patrol_jobs = [job for job in jobs if _job_has_patrol_signal(job)]
    patrol_cycle_index = max((job.patrol_cycle_count or 0) for job in jobs)
    latest_patrol_heartbeat = _max_aware_dt(
        *(job.last_patrol_heartbeat_at for job in jobs)
    )
    patrol_started_at = _min_aware_dt(
        stage_meta["patrol"]["started_at"],
        *(job.started_at for job in patrol_jobs),
        latest_patrol_heartbeat,
    )
    live_threshold = datetime.now(timezone.utc) - _LIVE_PATROL_HEARTBEAT_WINDOW
    patrol_active_devices = sum(
        1
        for job in jobs
        if job.last_patrol_heartbeat_at
        and _aware(job.last_patrol_heartbeat_at) >= live_threshold
    )
    events: list[EventOut] = []
    init_started_at = _aware(stage_meta["init"]["started_at"])
    init_ended_at = _aware(stage_meta["init"]["ended_at"])
    teardown_started_at = _aware(stage_meta["teardown"]["started_at"])
    teardown_ended_at = _aware(stage_meta["teardown"]["ended_at"])
    init_has_terminal_success = bool(stage_meta["init"]["has_terminal_success"])
    init_has_failure = bool(stage_meta["init"]["has_failure"])
    teardown_has_terminal_success = bool(stage_meta["teardown"]["has_terminal_success"])
    teardown_has_failure = bool(stage_meta["teardown"]["has_failure"])

    init_completed_at = _max_aware_dt(
        init_ended_at,
        init_started_at,
        patrol_started_at,
        pr.started_at,
    )
    if (
        patrol_started_at
        or teardown_started_at
        or (
            pr.status in _TERMINAL_PR_STATUSES
            and init_has_terminal_success
            and not init_has_failure
        )
    ):
        events.append(EventOut(
            ts=_iso(init_completed_at) or "",
            stage="init",
            severity="ok",
            category="system",
            title="INIT 完成",
            description="已进入后续执行阶段",
            ref={"type": "plan_run", "id": pr.id},
        ))

    if patrol_started_at:
        events.append(EventOut(
            ts=_iso(patrol_started_at) or "",
            stage="patrol",
            severity="info",
            category="system",
            title="PATROL 开始",
            description="巡检阶段已启动",
            ref={"type": "plan_run", "id": pr.id},
        ))

    patrol_progress_at = _max_aware_dt(
        latest_patrol_heartbeat,
        _aware(stage_meta["patrol"]["ended_at"]),
        _aware(stage_meta["patrol"]["started_at"]),
        *(job.started_at for job in patrol_jobs),
    )
    if pr.status not in _TERMINAL_PR_STATUSES and patrol_cycle_index > 0 and patrol_progress_at:
        heartbeat_window_minutes = int(_LIVE_PATROL_HEARTBEAT_WINDOW.total_seconds() // 60)
        patrol_desc = (
            f"最近 {heartbeat_window_minutes} 分钟内 {patrol_active_devices} 台设备上报心跳"
            if patrol_active_devices > 0
            else "已记录巡检进展"
        )
        events.append(EventOut(
            ts=_iso(patrol_progress_at) or "",
            stage="patrol",
            severity="info",
            category="system",
            title=f"PATROL 进行中 · 周期 #{patrol_cycle_index}",
            description=patrol_desc,
            ref={"type": "plan_run", "id": pr.id},
        ))

    if teardown_started_at:
        events.append(EventOut(
            ts=_iso(teardown_started_at) or "",
            stage="teardown",
            severity="info",
            category="system",
            title="TEARDOWN 开始",
            description="开始收尾清理",
            ref={"type": "plan_run", "id": pr.id},
        ))

    if teardown_ended_at and teardown_has_terminal_success and not teardown_has_failure:
        events.append(EventOut(
            ts=_iso(teardown_ended_at) or "",
            stage="teardown",
            severity="ok",
            category="system",
            title="TEARDOWN 完成",
            description="收尾阶段已结束",
            ref={"type": "plan_run", "id": pr.id},
        ))

    return [event for event in events if event.ts]


@router.get("/plan-runs/{run_id}/events", response_model=ApiResponse[PlanRunEventsOut])
def get_plan_run_events(
    run_id: int,
    stage: Optional[str] = Query(None, description="init / patrol / teardown / system / trigger / all"),
    severity: Optional[str] = Query(None, description="ok / info / warn / err / all"),
    limit: int = Query(_DEFAULT_EVENTS_LIMIT, ge=1, le=_MAX_EVENTS_LIMIT),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """ADR-0021/ADR-0022 C5a₂: 业务流事件流 — 融合 trigger / 合成阶段进展 /
    step_trace 失败 / log_signal / audit_logs,统一封装为 EventOut。

    支持 stage / severity 维度过滤;facets 始终基于"未过滤"的全集计算
    (前端过滤标签上的总数显示原本的总量,而 events 列表是当前过滤后的页)。
    """
    pr = _require_plan_run(db, run_id)
    jobs = db.execute(
        select(JobInstance).where(JobInstance.plan_run_id == run_id)
    ).scalars().all()
    job_ids = [j.id for j in jobs]
    job_to_device = {j.id: j.device_id for j in jobs}

    devices_by_id: dict[int, str] = {}
    if jobs:
        device_ids = list({j.device_id for j in jobs})
        rows = db.execute(
            select(Device.id, Device.serial).where(Device.id.in_(device_ids))
        ).all()
        devices_by_id = {r.id: r.serial for r in rows}

    stage_meta: dict[str, dict[str, object]] = {
        "init": {
            "started_at": None,
            "ended_at": None,
            "has_terminal_success": False,
            "has_failure": False,
        },
        "patrol": {
            "started_at": None,
            "ended_at": None,
            "has_terminal_success": False,
            "has_failure": False,
        },
        "teardown": {
            "started_at": None,
            "ended_at": None,
            "has_terminal_success": False,
            "has_failure": False,
        },
    }
    if job_ids:
        trace_rows = db.execute(
            select(
                StepTrace.stage,
                StepTrace.event_type,
                StepTrace.status,
                StepTrace.original_ts,
            ).where(StepTrace.job_id.in_(job_ids))
        ).all()
        for trace_stage, event_type, status, original_ts in trace_rows:
            if trace_stage not in stage_meta:
                continue
            bucket = stage_meta[trace_stage]
            started_at = bucket["started_at"]
            ended_at = bucket["ended_at"]
            if started_at is None or original_ts < started_at:
                bucket["started_at"] = original_ts
            if ended_at is None or original_ts > ended_at:
                bucket["ended_at"] = original_ts
            if event_type == "COMPLETED" and status in {"COMPLETED", "SKIPPED"}:
                bucket["has_terminal_success"] = True
            if event_type == "FAILED" or status in {"FAILED", "ABORTED"}:
                bucket["has_failure"] = True

    events: list[EventOut] = []

    # 1) trigger 事件 — PlanRun 自身
    events.append(EventOut(
        ts=_iso(pr.started_at) or "",
        stage="trigger",
        severity="ok",
        category="trigger",
        title=f"PlanRun #{pr.id} 启动",
        description=f"触发方式 {pr.run_type}" + (f" · 用户 {pr.triggered_by}" if pr.triggered_by else ""),
        ref={"type": "plan_run", "id": pr.id},
    ))

    # 2) 有限的阶段进展合成事件,避免活跃 patrol 只剩 trigger/异常事件
    events.extend(_build_synthetic_stage_events(pr, jobs, stage_meta))

    # 3) step_trace 失败 / abort 事件
    #    v3: 收紧 WHERE — 只取 event_type=FAILED 或 RUN_COMPLETE + terminal status;
    #    区分 ABORTED (warn + "Job 已中止") 与 FAILED (err + "Job 失败")
    if job_ids:
        bad_traces = db.execute(
            select(StepTrace)
            .where(
                (StepTrace.job_id.in_(job_ids))
                & (
                    (StepTrace.event_type == "FAILED")
                    | (
                        (StepTrace.event_type == "RUN_COMPLETE")
                        & (StepTrace.status.in_(["FAILED", "ABORTED"]))
                    )
                )
            )
            .order_by(StepTrace.original_ts.desc())
            .limit(_MAX_EVENTS_LIMIT)
        ).scalars().all()
        for t in bad_traces:
            dev_id = job_to_device.get(t.job_id)
            is_aborted = (
                t.step_id == "__job__"
                and t.event_type == "RUN_COMPLETE"
                and t.status == "ABORTED"
            )
            is_job_failed = (
                t.step_id == "__job__"
                and t.event_type == "RUN_COMPLETE"
                and t.status == "FAILED"
            )
            if is_aborted:
                title = f"Job #{t.job_id} 已中止"
                evt_severity = "warn"
                evt_stage = "system"
            elif is_job_failed:
                title = f"Job #{t.job_id} 失败"
                evt_severity = "err"
                evt_stage = "system"
            else:
                title = f"{t.stage}.{t.step_id} 失败"
                evt_severity = "err"
                evt_stage = t.stage if t.stage in {"init", "patrol", "teardown"} else "system"
            events.append(EventOut(
                ts=_iso(t.original_ts) or "",
                stage=evt_stage,
                severity=evt_severity,
                category="step",
                title=title,
                description=(t.error_message or "")[:512],
                job_id=t.job_id,
                device_id=dev_id,
                device_serial=devices_by_id.get(dev_id) if dev_id else None,
                ref={"type": "step_trace", "id": t.id},
            ))

    # 4) log_signal 事件(watcher 异常)
    if job_ids:
        signal_rows = db.execute(
            select(JobLogSignal)
            .where(JobLogSignal.job_id.in_(job_ids))
            .order_by(JobLogSignal.detected_at.desc())
            .limit(_MAX_EVENTS_LIMIT)
        ).scalars().all()
        for s in signal_rows:
            dev_id = job_to_device.get(s.job_id)
            events.append(EventOut(
                ts=_iso(s.detected_at) or "",
                stage="patrol",  # watcher signals 都视为 patrol 阶段事件
                severity=_log_signal_severity(s.category),
                category="log_signal",
                title=_log_signal_title(s.category),
                description=(s.first_lines or "")[:512] or s.path_on_device,
                job_id=s.job_id,
                device_id=dev_id,
                device_serial=devices_by_id.get(dev_id) or s.device_serial,
                ref={"type": "log_signal", "id": s.id},
            ))

    # 5) audit_logs(plan_run / job_instance / dispatch_gate)
    # AuditLog.resource_id 列宽到 String(64) 后(g0b1c2d3e4f5 迁移),需将整型主键
    # 转字符串再比较;PG 严格类型不会做隐式 varchar=int 转换。
    job_id_strs = [str(j) for j in (job_ids or [-1])]
    audit_q = select(AuditLog).where(
        ((AuditLog.resource_type == "plan_run") & (AuditLog.resource_id == str(run_id)))
        | ((AuditLog.resource_type == "job_instance") & (AuditLog.resource_id.in_(job_id_strs)))
    ).order_by(AuditLog.timestamp.desc()).limit(_MAX_EVENTS_LIMIT)
    for log in db.execute(audit_q).scalars().all():
        sev = "warn" if "abort" in (log.action or "") or "fail" in (log.action or "") else "info"
        events.append(EventOut(
            ts=_iso(log.timestamp) or "",
            stage="system",
            severity=sev,
            category="audit",
            title=log.action or "audit",
            description=str(log.details or {})[:512],
            ref={"type": "audit_log", "id": log.id},
        ))

    # 6) facets — 基于全集
    facets_stage: dict[str, int] = {}
    facets_sev:   dict[str, int] = {}
    for e in events:
        facets_stage[e.stage] = facets_stage.get(e.stage, 0) + 1
        facets_sev[e.severity] = facets_sev.get(e.severity, 0) + 1
    facets_stage["all"] = len(events)
    facets_sev["all"]   = len(events)

    # 7) 过滤
    filtered = events
    if stage and stage.lower() != "all":
        filtered = [e for e in filtered if e.stage == stage.lower()]
    if severity and severity.lower() != "all":
        filtered = [e for e in filtered if e.severity == severity.lower()]

    # 8) 按 ts 倒序 + 分页
    filtered.sort(key=lambda e: e.ts, reverse=True)
    total = len(filtered)
    page = filtered[offset: offset + limit]

    return ok(PlanRunEventsOut(
        plan_run_id=pr.id,
        events=page,
        total=total,
        facets={"by_stage": facets_stage, "by_severity": facets_sev},
    ))


# ── Endpoint 4: GET /plan-runs/{id}/devices ──────────────────────────────

class DeviceMatrixItem(BaseModel):
    device_id: int
    device_serial: Optional[str] = None
    device_model: Optional[str] = None
    host_id: Optional[str] = None
    job_id: int
    job_status: str
    ui_status: str                     # completed/running/failed/risk/backoff/pending
    current_stage: str
    current_step: Optional[str] = None
    patrol_cycle_count: int = 0
    patrol_success_cycle_count: int = 0
    patrol_failed_cycle_count: int = 0
    current_failure_streak: int = 0
    next_retry_at: Optional[str] = None
    manual_action: Optional[str] = None
    log_signal_count: int = 0
    last_heartbeat_at: Optional[str] = None
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    status_reason: Optional[str] = None   # ADR-0021: pending_timeout / agent never claimed / etc.


class PlanRunDevicesOut(BaseModel):
    plan_run_id: int
    total: int
    by_status: dict                    # {all/completed/running/failed/risk/backoff/pending: int}
    by_host: dict                      # {host_id: int}
    devices: list[DeviceMatrixItem]


def _ui_status_for_job(j: JobInstance, now: datetime) -> str:
    s = j.status
    if s == JobStatus.COMPLETED.value:
        return "completed"
    if s in _FAILED_JOB_STATUSES:
        return "failed"
    if s == JobStatus.PENDING.value:
        return "pending"
    # RUNNING 分支
    if (j.manual_action or "") == "EXIT_REQUESTED":
        return "backoff"
    nrt = _aware(j.next_retry_at)
    if nrt and nrt > now:
        return "backoff"
    if (j.log_signal_count or 0) > 0:
        return "risk"
    return "running"


def _current_stage_for_job(j: JobInstance) -> str:
    s = j.status
    if s == JobStatus.COMPLETED.value:
        return "done"
    if s in _FAILED_JOB_STATUSES:
        return "failed"
    if s == JobStatus.PENDING.value:
        return "pending"
    # RUNNING:有 patrol heartbeat 即视为 patrol;否则 init
    if (j.patrol_cycle_count or 0) > 0 or j.last_patrol_heartbeat_at:
        return "patrol"
    return "init"


@router.get("/plan-runs/{run_id}/devices", response_model=ApiResponse[PlanRunDevicesOut])
def get_plan_run_devices(
    run_id: int,
    status: Optional[str] = Query(None, description="ui_status 过滤(可选)"),
    host_id: Optional[str] = Query(None, description="host_id 过滤(可选)"),
    db: Session = Depends(get_db),
):
    """ADR-0021/ADR-0022 C5a₂: 设备执行矩阵 — 每台设备一行,
    包含 patrol 心跳聚合、退避状态、watcher 异常计数。

    by_status / by_host facet 始终基于"未过滤"的全集计算,
    便于前端筛选 chip 同时显示总数和当前数。
    """
    _require_plan_run(db, run_id)
    jobs = db.execute(
        select(JobInstance).where(JobInstance.plan_run_id == run_id)
    ).scalars().all()
    if not jobs:
        return ok(PlanRunDevicesOut(
            plan_run_id=run_id, total=0,
            by_status={"all": 0}, by_host={}, devices=[],
        ))

    # device 元数据
    device_ids = list({j.device_id for j in jobs})
    device_meta: dict[int, tuple[str, Optional[str]]] = {}
    if device_ids:
        rows = db.execute(
            select(Device.id, Device.serial, Device.model).where(Device.id.in_(device_ids))
        ).all()
        device_meta = {r.id: (r.serial, r.model) for r in rows}

    now = datetime.now(timezone.utc)
    items: list[DeviceMatrixItem] = []
    by_status: dict[str, int] = {"all": 0}
    by_host: dict[str, int] = {}

    for j in jobs:
        ui = _ui_status_for_job(j, now)
        cur_stage = _current_stage_for_job(j)
        serial, model = device_meta.get(j.device_id, (None, None))
        items.append(DeviceMatrixItem(
            device_id=j.device_id,
            device_serial=serial,
            device_model=model,
            host_id=j.host_id,
            job_id=j.id,
            job_status=j.status,
            ui_status=ui,
            current_stage=cur_stage,
            current_step=j.current_patrol_step,
            patrol_cycle_count=j.patrol_cycle_count or 0,
            patrol_success_cycle_count=j.patrol_success_cycle_count or 0,
            patrol_failed_cycle_count=j.patrol_failed_cycle_count or 0,
            current_failure_streak=j.current_failure_streak or 0,
            next_retry_at=_iso(j.next_retry_at),
            manual_action=j.manual_action,
            log_signal_count=j.log_signal_count or 0,
            last_heartbeat_at=_iso(j.last_patrol_heartbeat_at),
            started_at=_iso(j.started_at),
            ended_at=_iso(j.ended_at),
            status_reason=j.status_reason,
        ))
        by_status["all"] += 1
        by_status[ui] = by_status.get(ui, 0) + 1
        if j.host_id:
            by_host[j.host_id] = by_host.get(j.host_id, 0) + 1

    # 过滤(facets 已经基于全集)
    filtered = items
    if status and status.lower() != "all":
        filtered = [d for d in filtered if d.ui_status == status.lower()]
    if host_id and host_id.lower() != "all":
        filtered = [d for d in filtered if d.host_id == host_id]

    return ok(PlanRunDevicesOut(
        plan_run_id=run_id,
        total=len(items),
        by_status=by_status,
        by_host=by_host,
        devices=filtered,
    ))


# ── Endpoint 5: GET /plan-runs/{id}/watcher-summary ──────────────────────

class WatcherCategoryOut(BaseModel):
    category: str
    count: int
    affected_device_count: int
    trend_change: int                  # 当前窗口 - 上一窗口同长度
    latest_device_serial: Optional[str] = None
    latest_detected_at: Optional[str] = None


class WatcherSummaryOut(BaseModel):
    plan_run_id: int
    window_minutes: int
    window_start_at: str
    window_end_at: str
    categories: list[WatcherCategoryOut]
    total: int
    affected_device_count: int
    total_devices: int
    abnormal_rate: float               # affected_device_count / total_devices
    threshold: float
    exceeded: bool


@router.get(
    "/plan-runs/{run_id}/watcher-summary",
    response_model=ApiResponse[WatcherSummaryOut],
)
def get_plan_run_watcher_summary(
    run_id: int,
    window_minutes: int = Query(_DEFAULT_WATCHER_WINDOW_MIN, ge=1, le=_MAX_WATCHER_WINDOW_MIN),
    db: Session = Depends(get_db),
):
    """ADR-0018 / ADR-0021 C5a₂: 最近 N 分钟内 watcher log_signal 按 category
    聚合,带 trend(对比上一相同长度窗口的差值)。

    abnormal_rate = 当前窗口受影响设备数 / PlanRun 总设备数;
    与 PlanRun.failure_threshold 比较给出 exceeded 标志。
    """
    pr = _require_plan_run(db, run_id)

    job_rows = db.execute(
        select(JobInstance.id, JobInstance.device_id).where(JobInstance.plan_run_id == run_id)
    ).all()
    job_ids   = [r.id for r in job_rows]
    total_dev = len({r.device_id for r in job_rows})

    now = datetime.now(timezone.utc)
    window_delta = timedelta(minutes=window_minutes)
    cur_start  = now - window_delta
    prev_start = now - 2 * window_delta

    if not job_ids:
        return ok(WatcherSummaryOut(
            plan_run_id=pr.id,
            window_minutes=window_minutes,
            window_start_at=_iso(cur_start) or "",
            window_end_at=_iso(now) or "",
            categories=[], total=0, affected_device_count=0,
            total_devices=0, abnormal_rate=0.0,
            threshold=pr.failure_threshold, exceeded=False,
        ))

    # 当前窗口聚合(按 category 分组)
    cur_rows = db.execute(
        select(
            JobLogSignal.category,
            func.count(JobLogSignal.id),
            func.count(func.distinct(JobLogSignal.device_serial)),
            func.max(JobLogSignal.detected_at),
        )
        .where(
            (JobLogSignal.job_id.in_(job_ids))
            & (JobLogSignal.detected_at >= cur_start)
            & (JobLogSignal.detected_at <= now)
        )
        .group_by(JobLogSignal.category)
    ).all()

    # 上一窗口仅计 count(用于 trend)
    prev_rows = db.execute(
        select(JobLogSignal.category, func.count(JobLogSignal.id))
        .where(
            (JobLogSignal.job_id.in_(job_ids))
            & (JobLogSignal.detected_at >= prev_start)
            & (JobLogSignal.detected_at < cur_start)
        )
        .group_by(JobLogSignal.category)
    ).all()
    prev_counts = {row[0]: row[1] for row in prev_rows}

    # 找当前窗口 latest_device_serial
    latest_serial_by_cat: dict[str, str] = {}
    if cur_rows:
        latest_rows = db.execute(
            select(JobLogSignal.category, JobLogSignal.device_serial, JobLogSignal.detected_at)
            .where(
                (JobLogSignal.job_id.in_(job_ids))
                & (JobLogSignal.detected_at >= cur_start)
            )
            .order_by(JobLogSignal.detected_at.desc())
        ).all()
        for cat, serial, _ts in latest_rows:
            latest_serial_by_cat.setdefault(cat, serial)

    # 受影响设备数(去重所有 category)
    affected_total = db.execute(
        select(func.count(func.distinct(JobLogSignal.device_serial))).where(
            (JobLogSignal.job_id.in_(job_ids))
            & (JobLogSignal.detected_at >= cur_start)
        )
    ).scalar() or 0

    categories_out: list[WatcherCategoryOut] = []
    total = 0
    for cat, count, affected, latest_ts in cur_rows:
        total += count
        categories_out.append(WatcherCategoryOut(
            category=cat,
            count=count,
            affected_device_count=affected,
            trend_change=count - prev_counts.get(cat, 0),
            latest_device_serial=latest_serial_by_cat.get(cat),
            latest_detected_at=_iso(latest_ts),
        ))
    categories_out.sort(key=lambda c: c.count, reverse=True)

    abnormal_rate = (affected_total / total_dev) if total_dev else 0.0

    return ok(WatcherSummaryOut(
        plan_run_id=pr.id,
        window_minutes=window_minutes,
        window_start_at=_iso(cur_start) or "",
        window_end_at=_iso(now) or "",
        categories=categories_out,
        total=total,
        affected_device_count=affected_total,
        total_devices=total_dev,
        abnormal_rate=round(abnormal_rate, 4),
        threshold=pr.failure_threshold,
        exceeded=abnormal_rate > pr.failure_threshold,
    ))


@router.get("/plan-runs/{run_id}/summary", response_model=ApiResponse[dict])
def get_plan_run_summary(
    run_id: int,
    db: Session = Depends(get_db),
):
    pr = db.get(PlanRun, run_id)
    if pr is None:
        raise HTTPException(status_code=404, detail="plan run not found")

    jobs_result = db.execute(
        select(
            JobInstance.status,
            func.count(JobInstance.id),
        )
        .where(JobInstance.plan_run_id == run_id)
        .group_by(JobInstance.status)
    )
    status_counts = {row[0]: row[1] for row in jobs_result.all()}
    total = sum(status_counts.values())
    pass_rate = (
        status_counts.get("COMPLETED", 0) / total if total > 0 else 0.0
    )

    return ok({
        "plan_run_id": run_id,
        "status": pr.status,
        "total_jobs": total,
        "status_counts": status_counts,
        "pass_rate": round(pass_rate, 4),
        "started_at": _iso(pr.started_at),
        "ended_at": _iso(pr.ended_at),
        "result_summary": pr.result_summary,
    })


# ── Artifacts ────────────────────────────────────────────────────────────

@router.get(
    "/plan-runs/{run_id}/jobs/{job_id}/artifacts",
    response_model=ApiResponse[list],
)
def list_job_artifacts(
    run_id: int,
    job_id: int,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    job = db.get(JobInstance, job_id)
    if job is None or job.plan_run_id != run_id:
        raise HTTPException(status_code=404, detail="job not found in this plan run")

    result = db.execute(
        select(JobArtifact).where(JobArtifact.job_id == job_id)
    )
    artifacts = result.scalars().all()
    return ok([
        {
            "id": a.id,
            "job_id": a.job_id,
            "filename": a.storage_uri.rsplit("/", 1)[-1] if a.storage_uri else None,
            "artifact_type": a.artifact_type,
            "size_bytes": a.size_bytes,
            "checksum": a.checksum,
            "created_at": _iso(a.created_at),
        }
        for a in artifacts
    ])


def _artifact_download_target(storage_uri: str) -> dict[str, str]:
    parsed = urlparse(storage_uri)
    scheme = parsed.scheme.lower()
    if scheme in {"http", "https"}:
        return {"kind": "redirect", "url": storage_uri}
    if scheme != "file":
        raise HTTPException(status_code=400, detail=f"unsupported artifact scheme: {scheme or 'empty'}")
    p = Path(("//" + parsed.netloc + unquote(parsed.path)) if parsed.netloc else unquote(parsed.path))
    if not p.exists() or not p.is_file():
        raise HTTPException(status_code=404, detail=f"artifact file not found: {p}")
    return {"kind": "local", "path": str(p)}


@router.get(
    "/plan-runs/{run_id}/jobs/{job_id}/artifacts/{artifact_id}/download",
)
def download_job_artifact(
    run_id: int,
    job_id: int,
    artifact_id: int,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    job = db.get(JobInstance, job_id)
    if job is None or job.plan_run_id != run_id:
        raise HTTPException(status_code=404, detail="job not found in this plan run")

    artifact = db.get(JobArtifact, artifact_id)
    if artifact is None or artifact.job_id != job_id:
        raise HTTPException(status_code=404, detail="artifact not found for this job")

    target = _artifact_download_target(artifact.storage_uri)
    if target["kind"] == "redirect":
        return RedirectResponse(url=target["url"], status_code=307)
    local_path = Path(target["path"])
    media_type = "application/gzip" if local_path.suffixes[-2:] == [".tar", ".gz"] else None
    return FileResponse(path=str(local_path), filename=local_path.name, media_type=media_type)
