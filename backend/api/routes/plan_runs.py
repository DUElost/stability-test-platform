"""PlanRun API — ADR-0020.

Provides PlanRun list/detail/jobs/summary endpoints.
"""

from __future__ import annotations

import logging
import time
from copy import deepcopy
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy import String, case, cast, func, or_, select, text
from sqlalchemy.orm import Session, joinedload

from backend.api.response import ApiResponse, ok
from backend.api.routes.auth import get_current_active_user, User
from backend.api.routes.plans import MAX_CHAIN_DEPTH
from backend.api.schemas.case_result import (
    TestCaseResultOut,
    TestCaseResultSummary,
    TestCaseResultsPayload,
)
from backend.api.schemas.plan_run import (
    AeeBreakdownOut,
    AeeDashboardSectionOut,
    ChainNodeOut,
    JobInstanceOut,
    JobManualActionIn,
    JobManualActionOut,
    PackageRankingOut,
    PackageStatOut,
    PackageSubtypeCountOut,
    PlanChainOut,
    PlanRunAbortIn,
    PlanRunDevicesOut,
    PlanRunEventsOut,
    PlanRunLogEventOut,
    PlanRunLogEventsOut,
    PlanRunDetailOut,
    PlanRunListPageOut,
    PlanRunListStatsOut,
    PlanRunTimelineOut,
    StepTraceOut,
    SubtypeDistributionOut,
    WatcherAgentOpsMetrics,
    WatcherArchiveOut,
    WatcherCategoryOut,
    WatcherPlatformBucketOut,
    WatcherSignalLinkStatsOut,
    WatcherSummaryOut,
)
from backend.core.artifact_paths import (
    ArtifactPathError,
    resolve_local_artifact_path,
)
from backend.core.aee_metadata import (
    infer_aee_subtype_from_paths,
    normalize_aee_subtype,
    normalize_package_name,
    parse_exp_main_summary,
)
from backend.core.database import get_db
from backend.core.metrics import (
    record_plan_run_devices_query_duration,
)
from backend.models.enums import JobStatus, PlanRunStatus
from backend.models.host import Device, Host
from backend.core.job_timeout_config import (
    PRECHECK_ACTIVE_STALE_SECONDS,
    PRECHECK_QUEUE_STALE_SECONDS,
)
from backend.models.job import JobArtifact, JobInstance, JobLogSignal, StepTrace
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun
from backend.models.project import TestProject
from backend.services.plan_run_timeline import build_plan_run_timeline
from backend.services.plan_run_event_feed import build_plan_run_events
from backend.services.plan_run_devices import (
    build_plan_run_devices,
)
# 既有测试可能从路由导入 UI 状态派生符号。
from backend.services.plan_run_devices import (  # noqa: F401
    _COORDINATOR_HEARTBEAT_TIMEOUT_SECONDS,
    _WAITING_EXECUTION_STATES,
    _adb_state_excluded,
    _current_stage_for_job,
    _derive_busy_reason,
    _grace_remaining_seconds,
    _job_exec_status_for_job,
    _not_reported_liveness_anchor,
    _pending_claim_deadline,
    _pending_claim_remaining_seconds,
    _running_heartbeat_deadline,
    _ui_status_for_job,
)
from backend.services.plan_run_manual import (
    manual_exit_job_sync,
    manual_retry_job_sync,
)
from backend.services.plan_run_abort import (
    PlanRunAbortError,
    abort_plan_run,
)
from backend.services.plan_precheck import (
    PlanRunDispatchRetryError,
    retry_plan_run_dispatch,
)
from backend.services.plan_run_export import (
    build_plan_run_export,
    plan_run_export_to_markdown,
)
from backend.services.device_log_event import (
    LATE_EVENT_GRACE,
    list_plan_run_device_log_event_platforms,
    list_plan_run_device_log_events,
)
from backend.services.log_observation import (
    ANOMALY_SIGNAL_CATEGORIES,
    aggregate_signal_link_stats,
)
from backend.services.case_result_ingest import list_plan_run_test_case_results
from backend.services.job_artifact_download import build_artifact_download_response

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["plan-runs"])
_WATCHER_TIME_SCOPE_TO_MINUTES: dict[str, int] = {
    "15m": 15,
    "1h": 60,
    "6h": 360,
    "24h": 1440,
}
_SUBTYPE_FIXED_ORDER = [
    "ANR",
    "JE",
    "NE",
    "SWT",
    "Fatal NE",
    "Fatal JE",
    "Combo EE",
    "Kernel API Dump",
    "System API Dump",
    "HWT",
    "HANG",
    "KE",
    "HW Reboot",
    "Modem EE",
    "OCP Reboot",
    "其他",
]


# ── Schemas ──────────────────────────────────────────────────────────────


# ── Helpers ──────────────────────────────────────────────────────────────

def _iso(v) -> str | None:
    if v is None:
        return None
    return v.isoformat()


def _project_run_context(pr: PlanRun) -> Optional[dict]:
    if not isinstance(pr.run_context, dict):
        return pr.run_context
    context = deepcopy(pr.run_context)
    state = context.get("dispatch_state")
    if not isinstance(state, dict):
        return context

    status = str(state.get("status") or "")
    anchor_raw = (
        state.get("started_at")
        if status == "running"
        else state.get("enqueued_at")
    )
    timeout_seconds = (
        PRECHECK_ACTIVE_STALE_SECONDS
        if status == "running"
        else PRECHECK_QUEUE_STALE_SECONDS
    )
    if status in {"queued", "running"} and anchor_raw:
        try:
            anchor = datetime.fromisoformat(
                str(anchor_raw).replace("Z", "+00:00")
            )
            deadline = _aware(anchor) + timedelta(seconds=timeout_seconds)
            state["deadline_at"] = deadline.isoformat()
            state["stale"] = datetime.now(timezone.utc) >= deadline
        except (TypeError, ValueError):
            state["deadline_at"] = None
            state["stale"] = False
    else:
        state["stale"] = False
    summary = pr.result_summary if isinstance(pr.result_summary, dict) else {}
    state["retryable"] = (
        pr.status == PlanRunStatus.FAILED.value
        and bool(summary.get("precheck_failed") or summary.get("dispatch_failed"))
    )
    return context


def _plan_run_capabilities(pr: PlanRun) -> dict:
    summary = pr.result_summary if isinstance(pr.result_summary, dict) else {}
    terminal = pr.status in {
        PlanRunStatus.SUCCESS.value,
        PlanRunStatus.PARTIAL_SUCCESS.value,
        PlanRunStatus.FAILED.value,
    }
    return {
        "abort": pr.status in (
            PlanRunStatus.RUNNING.value,
            # ADR-0026: QUEUED/PRECHECK abort → straight FAILED (no jobs)
            PlanRunStatus.QUEUED.value,
            PlanRunStatus.PRECHECK.value,
        ),
        "retry_dispatch": (
            pr.status == PlanRunStatus.FAILED.value
            and bool(
                summary.get("precheck_failed")
                or summary.get("dispatch_failed")
            )
        ),
        "final_archive": terminal,
    }


def _plan_run_out(
    pr: PlanRun,
    jobs: list[JobInstanceOut] | None = None,
    plan_name: str | None = None,
    device_count: int | None = None,
) -> PlanRunDetailOut:
    return PlanRunDetailOut(
        id=pr.id,
        plan_id=pr.plan_id,
        status=pr.status,
        failure_threshold=pr.failure_threshold,
        run_type=pr.run_type,
        triggered_by=pr.triggered_by,
        started_at=_iso(pr.started_at) or "",
        ended_at=_iso(pr.ended_at),
        result_summary=pr.result_summary,
        run_context=_project_run_context(pr),
        plan_snapshot=pr.plan_snapshot,
        parent_plan_run_id=pr.parent_plan_run_id,
        root_plan_run_id=pr.root_plan_run_id,
        chain_index=pr.chain_index or 0,
        next_plan_triggered=bool(pr.next_plan_triggered),
        plan_name=plan_name,
        project_key=pr.project.project_key if pr.project else None,
        capabilities=_plan_run_capabilities(pr),
        jobs=jobs or [],
        # Distinct devices, not JobInstance row count (#747). List and detail
        # share this fallback so multi-job-per-device runs do not inflate.
        device_count=(
            device_count
            if device_count is not None
            else len({j.device_id for j in (jobs or [])})
        ),
        queue_reason=pr.queue_reason,
        enqueued_at=_iso(pr.enqueued_at),
        next_admission_at=_iso(pr.next_admission_at),
        priority=pr.priority or 0,
    )


def _step_out(t: StepTrace) -> StepTraceOut:
    return StepTraceOut(
        id=t.id, job_id=t.job_id, step_id=t.step_id, stage=t.stage,
        event_type=t.event_type, status=t.status, output=t.output,
        error_message=t.error_message, exit_code=t.exit_code,
        metadata=t.step_metadata,
        original_ts=_iso(t.original_ts) or "",
        created_at=_iso(t.created_at) or "",
    )


def _job_out(job: JobInstance, traces: list, device_serial: str | None = None) -> JobInstanceOut:
    return JobInstanceOut(
        id=job.id, plan_run_id=job.plan_run_id, plan_id=job.plan_id,
        device_id=job.device_id, device_serial=device_serial,
        host_id=job.host_id, status=job.status,
        status_reason=job.status_reason,
        execution_state=job.execution_state,
        last_execution_heartbeat_at=_iso(job.last_execution_heartbeat_at),
        last_progress_at=_iso(job.last_progress_at),
        started_at=_iso(job.started_at),
        ended_at=_iso(job.ended_at),
        created_at=_iso(job.created_at),
        step_traces=[_step_out(t) for t in traces],
    )


# ── Endpoints ────────────────────────────────────────────────────────────

def _apply_plan_run_list_filters(
    stmt,
    *,
    plan_id: Optional[int],
    statuses: Optional[list[PlanRunStatus]],
    project_key: Optional[str],
    q: Optional[str],
    db: Session,
    join_plan_for_search: bool = False,
):
    """共享 list / count / stats 的过滤条件。project_key 未知 → 404。"""
    if plan_id is not None:
        stmt = stmt.where(PlanRun.plan_id == plan_id)
    if statuses:
        values = [s.value for s in statuses]
        if len(values) == 1:
            stmt = stmt.where(PlanRun.status == values[0])
        else:
            stmt = stmt.where(PlanRun.status.in_(values))
    if project_key is not None:
        if db.query(TestProject).filter(TestProject.project_key == project_key).first() is None:
            raise HTTPException(status_code=404, detail="project not found")
        stmt = stmt.join(TestProject, PlanRun.project_id == TestProject.id).where(
            TestProject.project_key == project_key
        )
    needle = (q or "").strip()
    if needle:
        pattern = f"%{needle}%"
        clauses = [
            PlanRun.triggered_by.ilike(pattern),
            cast(PlanRun.id, String).ilike(pattern),
        ]
        if join_plan_for_search:
            stmt = stmt.join(Plan, PlanRun.plan_id == Plan.id)
        clauses.append(Plan.name.ilike(pattern))
        stmt = stmt.where(or_(*clauses))
    return stmt


@router.get("/plan-runs", response_model=ApiResponse[PlanRunListPageOut])
def list_plan_runs(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    plan_id: Optional[int] = None,
    status: Optional[list[PlanRunStatus]] = Query(
        default=None,
        description="可重复：?status=QUEUED&status=PRECHECK",
    ),
    project_key: Optional[str] = Query(None, description="ADR-0029: filter by project key"),
    q: Optional[str] = Query(None, description="搜索 Run ID / Plan 名 / 触发者"),
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    needle = (q or "").strip()
    need_plan_join = bool(needle)

    base = select(PlanRun).options(joinedload(PlanRun.project))
    base = _apply_plan_run_list_filters(
        base,
        plan_id=plan_id,
        statuses=status,
        project_key=project_key,
        q=q,
        db=db,
        join_plan_for_search=need_plan_join,
    )
    base = base.order_by(PlanRun.started_at.desc())

    count_stmt = select(func.count()).select_from(PlanRun)
    count_stmt = _apply_plan_run_list_filters(
        count_stmt,
        plan_id=plan_id,
        statuses=status,
        project_key=project_key,
        q=q,
        db=db,
        join_plan_for_search=need_plan_join,
    )
    total = int(db.execute(count_stmt).scalar_one())

    stats_stmt = select(PlanRun.status, func.count()).group_by(PlanRun.status)
    stats_stmt = _apply_plan_run_list_filters(
        stats_stmt,
        plan_id=plan_id,
        statuses=None,
        project_key=project_key,
        q=None,
        db=db,
        join_plan_for_search=False,
    )
    by_status = {row[0]: int(row[1]) for row in db.execute(stats_stmt).all()}
    stats = PlanRunListStatsOut(
        total=sum(by_status.values()),
        running=by_status.get(PlanRunStatus.RUNNING.value, 0),
        failed=by_status.get(PlanRunStatus.FAILED.value, 0),
    )

    runs = db.execute(base.offset(skip).limit(limit)).scalars().unique().all()
    plan_ids = {r.plan_id for r in runs}
    plan_names: dict[int, str] = {}
    if plan_ids:
        plan_rows = db.execute(
            select(Plan.id, Plan.name).where(Plan.id.in_(plan_ids))
        ).all()
        plan_names = {row.id: row.name for row in plan_rows}
    run_ids = [r.id for r in runs]
    device_counts: dict[int, int] = {}
    if run_ids:
        # Align with watcher-summary / UI「设备」列: distinct device_id (#747).
        count_rows = db.execute(
            select(
                JobInstance.plan_run_id,
                func.count(func.distinct(JobInstance.device_id)),
            )
            .where(JobInstance.plan_run_id.in_(run_ids))
            .group_by(JobInstance.plan_run_id)
        ).all()
        device_counts = {int(rid): int(cnt) for rid, cnt in count_rows}
    items = [
        _plan_run_out(
            r,
            plan_name=plan_names.get(r.plan_id),
            device_count=device_counts.get(r.id, 0),
        )
        for r in runs
    ]
    return ok(
        PlanRunListPageOut(
            items=items,
            total=total,
            skip=skip,
            limit=limit,
            stats=stats,
        )
    )


@router.get("/plan-runs/{run_id}", response_model=ApiResponse[PlanRunDetailOut])
def get_plan_run(
    run_id: int,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    pr = db.get(PlanRun, run_id)
    if pr is None:
        raise HTTPException(status_code=404, detail="plan run not found")
    jobs = db.execute(
        select(JobInstance).where(JobInstance.plan_run_id == run_id)
    ).scalars().all()
    plan_name: str | None = None
    if pr.plan_id is not None:
        plan_row = db.execute(
            select(Plan.name).where(Plan.id == pr.plan_id)
        ).scalar_one_or_none()
        plan_name = plan_row
    return ok(_plan_run_out(pr, jobs=[_job_out(j, []) for j in jobs], plan_name=plan_name))


@router.get("/plan-runs/{run_id}/jobs", response_model=ApiResponse[list[JobInstanceOut]])
def list_plan_run_jobs(
    run_id: int,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
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

    Returns 409 if the PlanRun is already terminal. PENDING jobs become
    ABORTED immediately; RUNNING jobs retain their lease until the Agent kills
    the process tree and ACKs ABORTED through the completion endpoint.
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
            raise HTTPException(status_code=404, detail=msg) from exc
        raise HTTPException(status_code=409, detail=msg) from exc
    return ok(summary)


# ── ADR-0025 S2: 手动归档(立即触发已终态 Job 的运行日志归档) ─────────────────

@router.post(
    "/plan-runs/{run_id}/archive",
    response_model=ApiResponse[dict],
)
async def archive_plan_run_logs_endpoint(
    run_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """ADR-0025 S2: 手动触发该 PlanRun 涉及 host 的运行日志立即归档 + scan。

    经 SocketIO control 向各 ONLINE host 的 Agent 下发:
      - archive_now: Agent ``scan_once(grace_seconds=0)``；实际仍受 ``MIN_GRACE_SECONDS``
        （300s）下限约束，并跳过 active Job，防止刚启动 Job 被误 prune。
      - scan_now: 向本 PlanRun 涉及的全部 host 下发同一份设备 serial 列表
        （可跨主机），Agent 只扫本地 HDD 上命中的 `{folder}/{serial}/`。
    归档和 scan 均为异步——返回「已触发」，前端应轮询/refetch。

    ADR-0038 D5：回收类允许触达退役主机，但**仅显式 admin 触发**（admin 会话
    allow_retired=True）+ 审计 + `skipped_retired` 如实报告（不虚报完整）。
    """
    from backend.core.audit import record_audit
    from backend.realtime.socketio_server import emit_agent_control
    from backend.services.plan_run_scan_scope import (
        build_scan_now_payload,
        classify_recycle_targets,
        iter_plan_run_scan_hosts,
    )

    pr = db.get(PlanRun, run_id)
    if pr is None:
        raise HTTPException(status_code=404, detail="plan run not found")

    has_jobs = (
        db.query(JobInstance.id)
        .filter(JobInstance.plan_run_id == run_id)
        .first()
    )
    if not has_jobs:
        raise HTTPException(status_code=400, detail="no jobs found for this plan run")

    allow_retired = current_user.role == "admin"
    host_rows = iter_plan_run_scan_hosts(db, run_id)
    if not host_rows:
        raise HTTPException(status_code=400, detail="no jobs found for this plan run")

    targets, skipped_offline, skipped_retired = classify_recycle_targets(
        host_rows, allow_retired=allow_retired,
    )

    triggered: list[str] = []
    for host_id in targets:
        await emit_agent_control(
            host_id, "archive_now",
            payload={"plan_run_id": run_id},
        )
        await emit_agent_control(
            host_id, "scan_now",
            payload=build_scan_now_payload(db, run_id, host_id, is_final=False),
        )
        triggered.append(host_id)

    record_audit(
        db,
        action="plan_run_archive_scan_trigger",
        resource_type="plan_run",
        resource_id=str(run_id),
        details={
            "triggered_hosts": triggered,
            "skipped_offline": skipped_offline,
            "skipped_retired": skipped_retired,
            "allow_retired": allow_retired,
        },
        user_id=current_user.id,
        username=current_user.username,
        request=request,
    )
    db.commit()

    return ok({
        "plan_run_id": run_id,
        "archived_now": True,
        "triggered_hosts": triggered,
        "skipped_offline": skipped_offline,
        "skipped_retired": skipped_retired,
    })


@router.post(
    "/plan-runs/{run_id}/retry-dispatch",
    response_model=ApiResponse[dict],
)
def retry_plan_run_dispatch_endpoint(
    run_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Return a failed precheck / dispatch run to the admission queue."""
    try:
        summary = retry_plan_run_dispatch(
            run_id,
            db=db,
            triggered_by=current_user.username if current_user else "api",
            audit_user_id=current_user.id if current_user else None,
        )
    except PlanRunDispatchRetryError as exc:
        msg = str(exc)
        if "not found" in msg:
            raise HTTPException(status_code=404, detail=msg) from exc
        if "queue unavailable" in msg:
            raise HTTPException(status_code=503, detail=msg) from exc
        raise HTTPException(status_code=409, detail=msg) from exc
    return ok(summary)


# ── ADR-0022: Manual retry / exit for patrol-backoff jobs ───────────────────


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

    #1520 切片：业务逻辑（校验/迁移/审计/emit/提交）在
    ``services/plan_run_manual.py``；本端点只做解析 → 调服务 → 序列化。
    """
    job = manual_retry_job_sync(
        db, run_id, job_id,
        reason=(payload.reason if payload else None),
        actor_id=current_user.id if current_user else None,
        actor_username=current_user.username if current_user else None,
    )
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

    #1520 切片：业务逻辑在 ``services/plan_run_manual.py``；本端点只做
    解析 → 调服务 → 序列化。
    """
    job = manual_exit_job_sync(
        db, run_id, job_id,
        reason=(payload.reason if payload else None),
        actor_id=current_user.id if current_user else None,
        actor_username=current_user.username if current_user else None,
    )
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
#       FAILED                              → failed
#       ABORTED                             → aborted
#       UNKNOWN                              → unknown (grace / recovery window)
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

_DEFAULT_WATCHER_WINDOW_MIN   = 60
_MAX_WATCHER_WINDOW_MIN       = 1440  # 1 天
_MAX_EVENTS_LIMIT             = 500
_DEFAULT_EVENTS_LIMIT         = 100



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
def get_plan_run_chain(
    run_id: int,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    """ADR-0020 §6: PlanRun chain 上下文 — 沿 parent_plan_run_id 回溯到 root,
    再沿 next_plan_id 展开到链尾（2026-09-01 起展示完整未触发链）。

    返回的 nodes 列表按 chain_index 升序,包含:
      - 0..N 个 parent PlanRun (已触发)
      - 1 个 current PlanRun(is_current=True)
      - 0..N 个未触发的 next Plan 节点(plan_run_id=None, status='pending')——
        沿 next_plan_id 每个未触发链节一个占位节点;仅链首节点承载
        is_blocked/block_reason,后续节点固定 block_reason="等待前序 Plan 触发"
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

    # 3) next Plan 完整链:取 chain_runs 末尾 PlanRun 对应 Plan.next_plan_id,
    #    沿 next_plan_id 展开到链尾——未触发节点全部 pending 占位（2026-09-01
    #    「执行链未显示完整」反馈:原实现只显示 1 跳,后续链节丢失）。
    if chain_runs:
        tail = chain_runs[-1]
        tail_plan = db.get(Plan, tail.plan_id)
        if tail_plan and tail_plan.next_plan_id and not tail.next_plan_triggered:
            first_next = db.get(Plan, tail_plan.next_plan_id)
            if first_next is not None:
                # 推断 block 原因（仅第一个 next——后续 pending 等前序触发）
                blocked = False
                reason = None
                summary = tail.result_summary or {}
                chain_fail = (
                    summary.get("chain_dispatch_failed")
                    if isinstance(summary, dict)
                    else None
                )
                if isinstance(chain_fail, dict) and chain_fail.get("error"):
                    blocked = True
                    reason = f"下游 Plan 派发失败: {chain_fail['error']}"
                elif tail.status == PlanRunStatus.RUNNING.value:
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
                elif tail.status in (PlanRunStatus.SUCCESS.value, PlanRunStatus.PARTIAL_SUCCESS.value):
                    blocked = True
                    reason = "等待下游 Plan 自动派发"

                # #753: read path must mirror write-side DAG guards — a 2+ cycle
                # (API-bypassing SQL) previously hung this GET forever.
                cursor = first_next
                idx = (tail.chain_index or 0) + 1
                seen_plans: set[int] = {r.plan_id for r in chain_runs}
                depth = 0
                while cursor is not None:
                    if cursor.id in seen_plans:
                        break
                    depth += 1
                    if depth > MAX_CHAIN_DEPTH:
                        break
                    seen_plans.add(cursor.id)
                    nodes.append(ChainNodeOut(
                        plan_id=cursor.id,
                        plan_name=cursor.name,
                        plan_run_id=None,
                        status="pending",
                        chain_index=idx,
                        failure_threshold=cursor.failure_threshold,
                        is_blocked=blocked if cursor.id == first_next.id else False,
                        block_reason=(
                            reason if cursor.id == first_next.id else "等待前序 Plan 触发"
                        ),
                    ))
                    if cursor.next_plan_id is None:
                        break
                    cursor = db.get(Plan, cursor.next_plan_id)
                    idx += 1

    return ok(PlanChainOut(
        plan_run_id=pr.id,
        root_plan_run_id=root_id,
        nodes=nodes,
    ))


# ── Endpoint 2: GET /plan-runs/{id}/timeline ─────────────────────────────

@router.get("/plan-runs/{run_id}/timeline", response_model=ApiResponse[PlanRunTimelineOut])
def get_plan_run_timeline(
    run_id: int,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    """ADR-0021/ADR-0022 C5a₂: 业务流时间线 — 按 stage 聚合 step_trace。

    业务聚合见 ``backend.services.plan_run_timeline.build_plan_run_timeline``。
    """
    pr = _require_plan_run(db, run_id)
    return ok(build_plan_run_timeline(db, pr))


# ── Endpoint 3: GET /plan-runs/{id}/events ───────────────────────────────


@router.get("/plan-runs/{run_id}/events", response_model=ApiResponse[PlanRunEventsOut])
def get_plan_run_events(
    run_id: int,
    stage: Optional[str] = Query(None, description="init / patrol / teardown / system / trigger / all"),
    severity: Optional[str] = Query(None, description="ok / info / warn / err / all"),
    search: Optional[str] = Query(None, description="关键字,大小写不敏感,匹配 title / description / device_serial"),
    limit: int = Query(_DEFAULT_EVENTS_LIMIT, ge=1, le=_MAX_EVENTS_LIMIT),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    """ADR-0021/ADR-0022 C5a₂: 业务流事件流。

    聚合见 ``backend.services.plan_run_event_feed.build_plan_run_events``。
    """
    pr = _require_plan_run(db, run_id)
    return ok(build_plan_run_events(
        db,
        pr,
        stage=stage,
        severity=severity,
        search=search,
        limit=limit,
        offset=offset,
    ))


# ── Endpoint 4: GET /plan-runs/{id}/devices ──────────────────────────────


@router.get("/plan-runs/{run_id}/devices", response_model=ApiResponse[PlanRunDevicesOut])
def get_plan_run_devices(
    run_id: int,
    status: Optional[str] = Query(None, description="job_exec_status 过滤(可选)"),
    link_status: Optional[str] = Query(None, description="device_link_status 过滤(可选)"),
    host_id: Optional[str] = Query(None, description="host_id 过滤(可选)"),
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    """ADR-0021/ADR-0022 C5a₂: 设备执行矩阵 — 每台设备一行,
    包含 patrol 心跳聚合、退避状态、watcher 异常计数。

    「连接」与「执行」是两个正交维度,各有一组 facet:
    `by_status` 基于 `job_exec_status`(纯 Job 投影,不掺设备连接),
    `by_link_status` 基于 `device_link_status`(ADB / Host 可达性)。
    两组 facet 与 `by_host` 一样始终基于"未过滤"全集计算,
    便于前端筛选 chip 同时显示总数和当前数。
    """
    t0 = time.perf_counter()
    try:
        _require_plan_run(db, run_id)
        return ok(build_plan_run_devices(
            db,
            run_id,
            status=status,
            link_status=link_status,
            host_id=host_id,
        ))
    finally:
        record_plan_run_devices_query_duration(time.perf_counter() - t0)


# ── Endpoint 5: GET /plan-runs/{id}/watcher-summary ──────────────────────


# M0/PR #2: AEE 细分聚合(crash / vendor_crash / anr 互斥 + by_package)
# 数据来源:JobLogSignal.extra (JSONB);仅当 source='reconciler' 的 signal
# 携带完整 extra 字段(event_type/package_name/aee_ts/nfs_path/pull_source);
# 旧 inotifyd 路径 signal 没有 extra,自动落入 unknown 桶。


def _aggregate_watcher_platform_buckets(
    db: Session,
    *,
    job_ids: list[int],
    cur_start: datetime,
    window_end: datetime,
) -> list[WatcherPlatformBucketOut]:
    if not job_ids:
        return []
    from backend.core.dedup_platform import has_collection_impl
    rows = db.execute(
        select(
            func.coalesce(Device.platform, "UNKNOWN").label("platform"),
            JobLogSignal.category,
            func.count(JobLogSignal.id),
            func.count(func.distinct(JobLogSignal.device_serial)),
        )
        .select_from(JobLogSignal)
        .join(JobInstance, JobLogSignal.job_id == JobInstance.id)
        .join(Device, JobInstance.device_id == Device.id)
        .where(
            JobLogSignal.job_id.in_(job_ids),
            JobLogSignal.detected_at >= cur_start,
            JobLogSignal.detected_at <= window_end,
        )
        .group_by(Device.platform, JobLogSignal.category)
    ).all()
    by_platform: dict[str, list[tuple[str, int, int]]] = defaultdict(list)
    for platform, category, count, affected in rows:
        by_platform[str(platform or "UNKNOWN")].append(
            (str(category), int(count or 0), int(affected or 0)),
        )
    # #749：原「RUNNING 一次 + 全量一次」两次全量聚合合并为一次条件聚合——
    # ``count(distinct case ...)`` 只计非 NULL，等价于原来带 status 过滤的那次查询。
    participation_rows = db.execute(
        select(
            func.coalesce(Device.platform, "UNKNOWN").label("platform"),
            func.count(func.distinct(JobInstance.device_id)).label("participating"),
            func.count(
                func.distinct(
                    case(
                        (JobInstance.status == JobStatus.RUNNING.value,
                         JobInstance.device_id),
                        else_=None,
                    )
                )
            ).label("running"),
        )
        .select_from(JobInstance)
        .join(Device, JobInstance.device_id == Device.id)
        .where(JobInstance.id.in_(job_ids))
        .group_by(Device.platform)
    ).all()
    participating_by_platform = {
        str(platform or "UNKNOWN"): int(participating or 0)
        for platform, participating, _running in participation_rows
    }
    running_by_platform = {
        str(platform or "UNKNOWN"): int(running or 0)
        for platform, _participating, running in participation_rows
    }
    # #749：受影响设备数（每平台去重 device_serial）由「平台循环内 N 次查询」改为
    # 一次分组查询。分组键用原始 Device.platform（与 by_platform 同源，故平台集合一致），
    # Python 侧再归一成 "UNKNOWN"。
    affected_rows = db.execute(
        select(
            func.coalesce(Device.platform, "UNKNOWN").label("platform"),
            func.count(func.distinct(JobLogSignal.device_serial)),
        )
        .select_from(JobLogSignal)
        .join(JobInstance, JobLogSignal.job_id == JobInstance.id)
        .join(Device, JobInstance.device_id == Device.id)
        .where(
            JobLogSignal.job_id.in_(job_ids),
            JobLogSignal.detected_at >= cur_start,
            JobLogSignal.detected_at <= window_end,
        )
        .group_by(Device.platform)
    ).all()
    affected_by_platform = {
        str(platform or "UNKNOWN"): int(count or 0)
        for platform, count in affected_rows
    }
    all_platforms = (
        set(by_platform) | set(running_by_platform) | set(participating_by_platform)
    )

    buckets: list[WatcherPlatformBucketOut] = []
    for platform in sorted(all_platforms):
        platform_rows = by_platform.get(platform, [])
        categories_out = [
            WatcherCategoryOut(
                category=cat, count=count, affected_device_count=affected, trend_change=0,
            )
            for cat, count, affected in sorted(platform_rows, key=lambda r: -r[1])
        ]
        # 该平台无信号行时字典无键 → 取 0（等价于原 `else: affected_total = 0`）
        affected_total = affected_by_platform.get(platform, 0)
        buckets.append(
            WatcherPlatformBucketOut(
                platform=platform,
                categories=categories_out,
                total=sum(c.count for c in categories_out),
                affected_device_count=int(affected_total),
                running_device_count=running_by_platform.get(platform, 0),
                participating_device_count=participating_by_platform.get(platform, 0),
                # R4-b b1：让「平台未支持」在 UI 上可与「没有异常」区分。
                reconciler_supported=has_collection_impl(platform),
            )
        )
    return buckets


@router.get(
    "/plan-runs/{run_id}/watcher-summary",
    response_model=ApiResponse[WatcherSummaryOut],
)
def get_plan_run_watcher_summary(
    run_id: int,
    window_minutes: Optional[int] = Query(None, ge=1, le=_MAX_WATCHER_WINDOW_MIN),
    time_scope: Optional[str] = Query(None, pattern="^(all|15m|1h|6h|24h)$"),
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    """ADR-0018 / ADR-0021 C5a₂: 最近 N 分钟内 watcher log_signal 按 category
    聚合,带 trend(对比上一相同长度窗口的差值)。

    abnormal_rate = 当前窗口受影响设备数 / PlanRun 总设备数;
    与 PlanRun.failure_threshold 比较给出 exceeded 标志。
    """
    pr = _require_plan_run(db, run_id)

    job_rows = db.execute(
        select(JobInstance.id, JobInstance.device_id, JobInstance.host_id, JobInstance.status).where(JobInstance.plan_run_id == run_id)
    ).all()
    job_ids   = [r.id for r in job_rows]
    total_dev = len({r.device_id for r in job_rows})

    now = datetime.now(timezone.utc)
    resolved_scope, resolved_window_minutes, cur_start, window_end, prev_start = (
        _resolve_watcher_summary_window(
            pr,
            now=now,
            time_scope=time_scope,
            window_minutes=window_minutes,
        )
    )

    if not job_ids:
        return ok(WatcherSummaryOut(
            plan_run_id=pr.id,
            window_minutes=resolved_window_minutes,
            time_scope=resolved_scope,
            window_start_at=_iso(cur_start) or "",
            window_end_at=_iso(window_end) or "",
            categories=[], total=0, affected_device_count=0,
            total_devices=0, abnormal_rate=0.0,
            threshold=pr.failure_threshold, exceeded=False,
            supports_origin_split=False,
            current_run=_empty_dashboard_section(),
            preexisting=_empty_dashboard_section(),
            watcher_capability=None,
            archive=WatcherArchiveOut(
                link_stats=WatcherSignalLinkStatsOut(),
            ),
        ))

    # #556: read-only. Link repair is owned by the signal_link_reconcile sweep —
    # doing it here ran inside a get_db() session that is never committed, so it
    # never persisted while still locking rows on every poll.
    link_stats = WatcherSignalLinkStatsOut(
        **aggregate_signal_link_stats(db, job_ids),
    )

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
            & (JobLogSignal.detected_at <= window_end)
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
                & (JobLogSignal.detected_at <= window_end)
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
            & (JobLogSignal.detected_at <= window_end)
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

    supports_origin_split, current_run, preexisting = _aggregate_aee_dashboard_sections(
        db,
        job_ids=job_ids,
        cur_start=cur_start,
        window_end=window_end,
    )

    # M0/PR #2: AEE 细分(crash / vendor_crash / anr 互斥 + by_package)
    # PG-only(JSONB):未含 extra 的 legacy signal 自动落入 unknown package +
    # NULL nfs_path,通过 path_on_device 兜底为 ANR 去重键。
    aee_breakdown = _aggregate_aee_breakdown(
        db, job_ids=job_ids, cur_start=cur_start, now=window_end,
    )
    platform_buckets = _aggregate_watcher_platform_buckets(
        db, job_ids=job_ids, cur_start=cur_start, window_end=window_end,
    )
    watcher_capability = _aggregate_watcher_capability(db, job_ids=job_ids)

    return ok(WatcherSummaryOut(
        plan_run_id=pr.id,
        window_minutes=resolved_window_minutes,
        time_scope=resolved_scope,
        window_start_at=_iso(cur_start) or "",
        window_end_at=_iso(window_end) or "",
        categories=categories_out,
        total=total,
        affected_device_count=affected_total,
        total_devices=total_dev,
        abnormal_rate=round(abnormal_rate, 4),
        threshold=pr.failure_threshold,
        exceeded=abnormal_rate > pr.failure_threshold,
        supports_origin_split=supports_origin_split,
        current_run=current_run,
        preexisting=preexisting,
        aee_breakdown=aee_breakdown,
        platform_buckets=platform_buckets,
        watcher_capability=watcher_capability,
        archive=_aggregate_run_log_archive(
            db,
            job_rows=job_rows,
            total_jobs=len(job_ids),
            plan_run_id=run_id,
            link_stats=link_stats,
        ),
    ))


@router.get(
    "/plan-runs/{run_id}/log-events",
    response_model=ApiResponse[PlanRunLogEventsOut],
)
def get_plan_run_log_events(
    run_id: int,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    state: Optional[str] = Query(None, description="Filter by DLE state"),
    platform: Optional[str] = Query(None, description="Filter by device platform"),
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    """ADR-0028 / #529: PlanRun device log events — archive authority.

    Paths and upload state come from ``device_log_event`` (not watcher-summary
  signals). RUNNING PlanRuns may return a partial list; terminal runs are the
    primary consumer.
    """
    _require_plan_run(db, run_id)
    rows, total = list_plan_run_device_log_events(
        db,
        run_id,
        skip=skip,
        limit=limit,
        state=state,
        platform=platform,
    )
    items = [
        PlanRunLogEventOut(
            id=str(row.id),
            serial=row.serial,
            platform=row.platform,
            event_type=row.event_type,
            event_subtype=row.event_subtype,
            state=row.state,
            local_path=row.local_path,
            remote_path=row.remote_path,
            detected_at=_iso(row.detected_at) or "",
            device_timestamp=_iso(row.device_timestamp),
            job_id=row.job_id,
            host_id=row.host_id,
            signal_seq_no=row.signal_seq_no,
        )
        for row in rows
    ]
    return ok(PlanRunLogEventsOut(
        plan_run_id=run_id,
        total=total,
        items=items,
        # #2288：平台全集单独取——不受本次 `platform`/`limit` 影响，前端筛选选项据此渲染。
        platforms=list_plan_run_device_log_event_platforms(db, run_id, state=state),
    ))


@router.get(
    "/plan-runs/{run_id}/test-case-results",
    response_model=ApiResponse[TestCaseResultsPayload],
)
def get_plan_run_test_case_results(
    run_id: int,
    skip: int = Query(0, ge=0),
    limit: int = Query(500, ge=1, le=2000),
    status: Optional[str] = Query(None, description="PASS / FAILURE / ERROR"),
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    """ADR-0030 P2: PlanRun 逐条用例结果（test_case_result 表）。"""
    _require_plan_run(db, run_id)
    rows, total, summary_counts = list_plan_run_test_case_results(
        db, run_id, status=status, skip=skip, limit=limit,
    )
    job_ids = {row.job_id for row in rows}
    jobs_by_id: dict[int, JobInstance] = {}
    if job_ids:
        jobs_by_id = {
            j.id: j
            for j in db.query(JobInstance).filter(JobInstance.id.in_(job_ids)).all()
        }
    items = []
    for row in rows:
        job = jobs_by_id.get(row.job_id)
        items.append(TestCaseResultOut(
            id=row.id,
            plan_run_id=row.plan_run_id,
            job_id=row.job_id,
            suite_id=row.suite_id,
            case_id=row.case_id,
            case_name=row.case_name,
            status=row.status,
            detail=row.detail,
            artifact_uri=row.artifact_uri,
            run_dir=row.run_dir,
            created_at=row.created_at,
            device_id=job.device_id if job else None,
            host_id=str(job.host_id) if job and job.host_id is not None else None,
        ))
    return ok(TestCaseResultsPayload(
        items=items,
        total=total,
        summary=TestCaseResultSummary(**summary_counts),
    ))


# M0/C-6: watcher_capability 降级严重度排序 — 取该 PlanRun 下 Job 中"最降级"的一档,
# 使得只要有任一设备落到 reconciler 单通道(unavailable)即可在前端给出提示。
# 数值越大越降级;未知能力按 0 处理(不触发降级徽章)。
_CAPABILITY_SEVERITY: dict[str, int] = {
    "unavailable":       40,   # 探测全失败 → reconciler 单通道(目标徽章场景)
    "polling":           30,   # inotifyd 不可用,reconciler 承担拉+emit
    "inotifyd_shell":    20,
    "inotifyd_root":     10,
    "inotifyd_realtime": 10,
    "stub":               5,
    "skipped":           -10,  # watcher 未启动(非降级,前端不徽章)
}


def _aggregate_run_log_archive(
    db: Session,
    *,
    job_rows: list,
    total_jobs: int,
    plan_run_id: int,
    link_stats: Optional[WatcherSignalLinkStatsOut] = None,
) -> WatcherArchiveOut:
    host_ids = {r.host_id for r in job_rows if r.host_id}
    if not host_ids:
        return WatcherArchiveOut()

    hosts = db.execute(
        select(Host).where(Host.id.in_(host_ids))
    ).scalars().all()

    ops = WatcherAgentOpsMetrics()
    for host in hosts:
        extra = host.extra if isinstance(host.extra, dict) else {}
        archive = extra.get("archive")
        if not isinstance(archive, dict):
            continue
        ops.pruned_total += int(archive.get("pruned_total") or 0)
        ops.spill_cycles += int(archive.get("spill_cycles") or 0)
        ops.spilled_total += int(archive.get("spilled_total") or 0)
        host_pct = archive.get("local_disk_usage_pct")
        if host_pct is not None:
            try:
                host_pct_float = float(host_pct)
                if ops.local_disk_usage_pct is None or host_pct_float > ops.local_disk_usage_pct:
                    ops.local_disk_usage_pct = host_pct_float
            except (TypeError, ValueError):
                pass

    scan_status: Optional[str] = None
    scan_triggered_at: Optional[str] = None
    signaled_jobs = 0
    pending_jobs = 0
    failed_jobs = 0

    _TERMINAL_JOB = {"COMPLETED", "SUCCESS", "PARTIAL_SUCCESS", "FAILED", "ABORTED"}
    if job_rows:
        job_ids = [r.id for r in job_rows]
        job_statuses = {r.id: r.status for r in job_rows}

        from backend.models.plan_run_artifact import PlanRunArtifact
        from backend.models.job import JobLogSignal
        from sqlalchemy import func as sa_func

        signal_job_ids = set(
            row[0] for row in db.execute(
                select(func.distinct(JobLogSignal.job_id)).where(
                    JobLogSignal.job_id.in_(job_ids),
                )
            ).all()
        )

        for jid in job_ids:
            status = job_statuses.get(jid, "")
            if jid in signal_job_ids:
                signaled_jobs += 1
            elif status in _TERMINAL_JOB:
                failed_jobs += 1
            else:
                pending_jobs += 1

        merge_count = db.execute(
            select(sa_func.count(PlanRunArtifact.id)).where(
                PlanRunArtifact.plan_run_id == plan_run_id,
                PlanRunArtifact.artifact_type == "merge_result_xls",
            )
        ).scalar_one()
        scan_count = db.execute(
            select(sa_func.count(PlanRunArtifact.id)).where(
                PlanRunArtifact.plan_run_id == plan_run_id,
                PlanRunArtifact.artifact_type == "scan_result_xls",
            )
        ).scalar_one()

        if merge_count > 0:
            scan_status = "merged"
            first = db.execute(
                select(PlanRunArtifact.created_at).where(
                    PlanRunArtifact.plan_run_id == plan_run_id,
                    PlanRunArtifact.artifact_type == "merge_result_xls",
                ).order_by(PlanRunArtifact.created_at.asc()).limit(1)
            ).scalar_one_or_none()
        elif scan_count > 0:
            scan_status = "scanned"
            first = db.execute(
                select(PlanRunArtifact.created_at).where(
                    PlanRunArtifact.plan_run_id == plan_run_id,
                    PlanRunArtifact.artifact_type == "scan_result_xls",
                ).order_by(PlanRunArtifact.created_at.asc()).limit(1)
            ).scalar_one_or_none()
        else:
            scan_status = "pending"
            first = None

        if first is not None:
            scan_triggered_at = first.isoformat() if hasattr(first, "isoformat") else str(first)

    return WatcherArchiveOut(
        ops_metrics=ops,
        scan_status=scan_status,
        scan_triggered_at=scan_triggered_at,
        signaled_jobs=signaled_jobs,
        pending_jobs=pending_jobs,
        failed_jobs=failed_jobs,
        link_stats=link_stats,
    )


def _aggregate_watcher_capability(db: Session, *, job_ids: list[int]) -> Optional[str]:
    """C-6 (§2.4 #5): 汇总该 PlanRun 下 Job 的 watcher 能力快照。

    取 JobInstance.watcher_capability 列中"最降级"的一档(按 _CAPABILITY_SEVERITY);
    全部为 NULL(Agent 未回填)时返回 None。该值仅用于前端提示,不参与聚合计数,
    因此跨方言(PG / SQLite)均可工作。
    """
    if not job_ids:
        return None
    rows = db.execute(
        select(JobInstance.watcher_capability)
        .where(JobInstance.id.in_(job_ids))
        .where(JobInstance.watcher_capability.isnot(None))
    ).all()
    caps = [str(r[0]) for r in rows if r[0]]
    if not caps:
        return None
    return max(caps, key=lambda c: _CAPABILITY_SEVERITY.get(c, 0))


# ── ADR-0025 Sprint 3: crash 详情端点（按事件目录组织）──────────────────────

@router.get(
    "/plan-runs/{run_id}/crash-details",
    response_model=ApiResponse[list],
)
def get_plan_run_crash_details(
    run_id: int,
    package_name: Optional[str] = Query(None, description="按包名过滤"),
    time_scope: Optional[str] = Query(None, pattern="^(all|15m|1h|6h|24h)$"),
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    """ADR-0025 Sprint 3 步骤 4: 按 package_name 返回 crash 事件详情列表。

    复用 watcher-summary 的去重逻辑 (_load_deduped_aee_events)，
    按 package_name 过滤后返回事件级详情（含 nfs_path / event_type / device_serial / job_id）。
    """
    pr = _require_plan_run(db, run_id)

    job_rows = db.execute(
        select(JobInstance.id).where(JobInstance.plan_run_id == run_id)
    ).all()
    job_ids = [r.id for r in job_rows]
    if not job_ids:
        return ok([])

    now = datetime.now(timezone.utc)
    resolved_scope, _resolved_minutes, cur_start, window_end, _prev_start = (
        _resolve_watcher_summary_window(
            pr,
            now=now,
            time_scope=time_scope,
            window_minutes=None,
        )
    )

    events = _load_deduped_aee_events(
        db,
        job_ids=job_ids,
        cur_start=cur_start,
        window_end=window_end,
    )

    result = []
    for event in events:
        pkg = event.get("package_name") or "unknown"
        if package_name and pkg != package_name:
            continue
        result.append({
            "package_name": pkg,
            "subtype": event.get("subtype"),
            "group": event.get("group"),
            "device_serial": event.get("device_serial"),
            "detected_at": _iso(event.get("detected_at")),
            "entry_origin": event.get("entry_origin"),
        })

    result.sort(key=lambda e: e.get("detected_at") or "", reverse=True)
    return ok(result)


def _resolve_watcher_summary_window(
    pr: PlanRun,
    *,
    now: datetime,
    time_scope: Optional[str],
    window_minutes: Optional[int],
) -> tuple[str, Optional[int], datetime, datetime, datetime]:
    window_end = pr.ended_at if pr.ended_at else now
    if window_end.tzinfo is None:
        window_end = window_end.replace(tzinfo=timezone.utc)
    run_start = pr.started_at or window_end
    if run_start.tzinfo is None:
        run_start = run_start.replace(tzinfo=timezone.utc)

    # #1962：只有「窗口本身就是 run 窗口」的场景才加末端宽限。相对范围
    # （15m/1h/… 与 legacy window_minutes）是**相对切片**，其末端不随 run 结束
    # 延展——否则「15m」会静默变成 45m，标签说谎。
    covers_run_window = False

    if time_scope:
        if time_scope == "all":
            cur_start = run_start
            resolved_minutes = None
            covers_run_window = True
        else:
            resolved_minutes = _WATCHER_TIME_SCOPE_TO_MINUTES[time_scope]
            cur_start = max(run_start, window_end - timedelta(minutes=resolved_minutes))
        resolved_scope = time_scope
    elif window_minutes is not None:
        # Legacy window_minutes keeps the historical rolling-window semantics and
        # may include signals emitted before this PlanRun started.
        cur_start = window_end - timedelta(minutes=window_minutes)
        resolved_minutes = window_minutes
        resolved_scope = _window_minutes_to_scope_label(window_minutes)
    else:
        cur_start = run_start
        resolved_minutes = None
        resolved_scope = "all"
        covers_run_window = True

    delta = max(window_end - cur_start, timedelta(minutes=1))
    # 趋势基线按**未加宽限**的窗口推算，避免放宽末端后基线漂移。
    prev_start = cur_start - delta

    # #1962：窗口即 run 窗口时，末端加宽限。reconciler 的首个 tick 需要 ls + pull
    # 若干事件目录，可能**慢于 job 本身的生命周期**（实测 run 393 的 job 只跑了
    # 11s，事件在其后 70s 才落库），而 DLE 视图按 plan_run_id 取数不受窗口限制
    # ——于是出现「DLE 有行、仪表盘 0」。宽限值与 device_log_event 的
    # 「PlanRun 窗口附近的迟到落库」语义同一来源（LATE_EVENT_GRACE）。
    # 相对范围不加宽限（见上）；RUNNING run（无 ended_at）亦不受影响。
    if covers_run_window and pr.ended_at is not None:
        window_end = window_end + LATE_EVENT_GRACE

    return resolved_scope, resolved_minutes, cur_start, window_end, prev_start


def _window_minutes_to_scope_label(window_minutes: int) -> str:
    for scope, minutes in _WATCHER_TIME_SCOPE_TO_MINUTES.items():
        if minutes == window_minutes:
            return scope
    return f"{window_minutes}m"


def _empty_dashboard_section() -> AeeDashboardSectionOut:
    return AeeDashboardSectionOut(
        total_events=0,
        affected_device_count=0,
        top_package_name=None,
        top_subtype=None,
        subtype_distribution=[],
        package_ranking=[],
    )


def _aggregate_aee_dashboard_sections(
    db: Session,
    *,
    job_ids: list[int],
    cur_start: datetime,
    window_end: datetime,
) -> tuple[bool, AeeDashboardSectionOut, AeeDashboardSectionOut]:
    events = _load_deduped_aee_events(
        db,
        job_ids=job_ids,
        cur_start=cur_start,
        window_end=window_end,
    )
    supports_origin_split = all(
        event["entry_origin"] in {"baseline", "runtime"} for event in events
    )
    if not supports_origin_split:
        return (
            False,
            _build_dashboard_section(events),
            _empty_dashboard_section(),
        )
    runtime_events = [event for event in events if event["entry_origin"] == "runtime"]
    baseline_events = [event for event in events if event["entry_origin"] == "baseline"]
    return (
        True,
        _build_dashboard_section(runtime_events),
        _build_dashboard_section(baseline_events),
    )


def _load_deduped_aee_events(
    db: Session,
    *,
    job_ids: list[int],
    cur_start: datetime,
    window_end: datetime,
) -> list[dict[str, Any]]:
    if not job_ids:
        return []

    rows = db.execute(
        select(
            JobLogSignal.id,
            JobLogSignal.category,
            JobLogSignal.device_serial,
            JobLogSignal.path_on_device,
            JobLogSignal.artifact_uri,
            JobLogSignal.detected_at,
            JobLogSignal.extra,
        )
        .where(JobLogSignal.job_id.in_(job_ids))
        .where(JobLogSignal.detected_at >= cur_start)
        .where(JobLogSignal.detected_at <= window_end)
        # #1956：类别口径与风险汇总共用真源。此前这里硬编码三元组，
        # 导致 UNIVIEW（展锐）事件虽已入库却不进仪表盘。
        .where(JobLogSignal.category.in_(ANOMALY_SIGNAL_CATEGORIES))
    ).all()

    deduped: dict[str, dict[str, Any]] = {}
    for row in rows:
        extra = row.extra if isinstance(row.extra, dict) else {}
        package_name = _infer_dashboard_package_name(
            extra,
            artifact_uri=row.artifact_uri,
        )
        group, subtype = _infer_dashboard_event_group_and_subtype(
            row.category,
            extra,
            path_on_device=row.path_on_device,
            artifact_uri=row.artifact_uri,
        )
        entry_origin = _normalize_entry_origin(extra.get("entry_origin"))
        key = _aee_event_dedup_key(
            row.id, row.category, row.path_on_device, extra,
            device_serial=row.device_serial or "",
        )
        candidate = {
            "key": key,
            "group": group,
            "subtype": subtype,
            "package_name": package_name,
            "device_serial": row.device_serial,
            "detected_at": row.detected_at,
            "entry_origin": entry_origin,
        }
        existing = deduped.get(key)
        if existing is None or _prefer_deduped_event(candidate, existing):
            deduped[key] = candidate
    return list(deduped.values())


def _prefer_deduped_event(candidate: dict[str, Any], existing: dict[str, Any]) -> bool:
    if bool(candidate["entry_origin"]) != bool(existing["entry_origin"]):
        return bool(candidate["entry_origin"])
    if candidate["package_name"] != "unknown" and existing["package_name"] == "unknown":
        return True
    return candidate["detected_at"] > existing["detected_at"]


def _normalize_entry_origin(value: Any) -> Optional[str]:
    normalized = str(value or "").strip().lower()
    if normalized in {"baseline", "runtime"}:
        return normalized
    return None


def _aee_event_dedup_key(
    signal_id: int,
    category: str,
    path_on_device: str,
    extra: dict[str, Any],
    device_serial: str = "",
) -> str:
    nfs_path = str(extra.get("nfs_path") or "").strip()
    # #1956：UNIVIEW 与 AEE 同用 nfs_path 去重——同一物理事件被多次 run 拉取时只算一次。
    #
    # #2080：UNIVIEW 的 `nfs_path` 是**事件目录**（一个目录可容纳多个异常），不是单条
    # 异常的物理路径；对 AEE/MTK 而言目录键等价于事件键，对 UNIVIEW 则不唯一。
    # Agent 侧 #2010 已改为「签名变化就再发射一条」（同一目录可产出多条信号），若消费侧
    # 仍按目录去重，会把它们**并回一条**——即 #2010 修好的症状在消费侧残留。
    # 故 UNIVIEW 键补上事件身份；AEE / VENDOR_AEE 保持目录键不动（其 nfs_path 指向
    # 单事件产物，目录键本已唯一）。
    if category == "UNIVIEW" and nfs_path:
        return _uniview_dedup_key(nfs_path, extra, device_serial=device_serial)
    if category in {"AEE", "VENDOR_AEE"} and nfs_path:
        return f"nfs:{nfs_path}"
    path = str(path_on_device or "").strip()
    if path:
        return f"path:{path}"
    return f"id:{signal_id}"


def _uniview_dedup_key(
    nfs_path: str, extra: dict[str, Any], *, device_serial: str = "",
) -> str:
    """UNIVIEW 去重键：**serial + 事件目录 + 事件身份**，形态恒定四段（#2080/#2285/#2394）。

    #2394-②：身份段**不含日期根**。``nfs_path`` 的存储布局
    ``…/uniview_watcher/{MMDD}/{serial}/{event_dir}`` 里的 ``{MMDD}`` 随 run 日期漂移——
    同一条物理事件若跨天被重放（如 agent 状态丢失后重新拉取），旧键会因日期段不同而
    裂成两行（C9 双计）。serial 取自行列（非路径反解），目录名取路径末段，日期根整体
    不再参与键。读侧派生、非持久列 → 新旧行同函数同规则，一次性切换，无存量迁移。

    前缀 ``uniview:`` 与 AEE 家族键（``nfs:…``）天然不同形，保住 #2285
    「UNIVIEW 行与 AEE 行永不互并」的不变量；字段缺失时仍以空占位保形（宁多勿并）。
    serial 或目录名不可得时**退回旧式全路径键**（防御异常数据，不抛错不并错）。

    事件身份取 ``event_subtype`` + ``aee_ts``——二者由 Agent 侧
    ``unisoc_reconciler._emit_event`` 一并写入 ``extra``（``aee_ts`` 为设备时钟原文，
    #785）；同目录内不同异常至少有一项不同。同一条异常被多次 run 拉取时二者不变，
    故仍能正确去重。

    #2285：两项皆缺时**不再**退化为两段键 ``nfs:{dir}`` —— 那与 AEE / VENDOR_AEE
    家族的键（``_aee_event_dedup_key`` 的 ``f"nfs:{nfs_path}"``）**同形**，同目录的
    AEE 行与 UNIVIEW 行会被并成一条（#2010「签名变化就再发射一条」在消费侧的反向
    残留）。恒带两个占位后，UNIVIEW 键与 AEE 键不可能相等。

    有字段 / 无字段仍是两个身份：字段缺失时无法安全归并——把同目录的未知身份行并成
    一条会把**不同异常**算成一次（欠计数），与 #2080「宁多勿并」的取向一致。
    """
    subtype = str(extra.get("event_subtype") or "").strip()
    aee_ts = str(extra.get("aee_ts") or "").strip()
    serial = str(device_serial or "").strip()
    event_dir = nfs_path.rstrip("/").rsplit("/", 1)[-1].strip()
    if not serial or not event_dir:
        return f"nfs:{nfs_path}#{subtype}#{aee_ts}"
    return f"uniview:{serial}#{event_dir}#{subtype}#{aee_ts}"


def _infer_dashboard_event_group_and_subtype(
    category: str,
    extra: dict[str, Any],
    *,
    path_on_device: str = "",
    artifact_uri: Optional[str] = None,
) -> tuple[str, str]:
    event_subtype = str(extra.get("event_subtype") or "").strip()
    if event_subtype:
        subtype = event_subtype
    else:
        raw_event_type = str(extra.get("raw_event_type") or "").strip()
        event_type = str(extra.get("event_type") or "").strip().upper()
        subtype = _normalize_dashboard_subtype(
            raw_event_type,
            event_type,
            category,
            path_on_device=path_on_device,
            artifact_uri=artifact_uri,
            nfs_path=str(extra.get("nfs_path") or "").strip(),
        )

    # #1956：展锐（UNIVIEW）自成一组，且必须**先**于下面的 ANR / VENDOR 判定，
    # 否则 UNIVIEW 的 ANR 会被并进 MTK 的 AEE/ANR 桶，混平台后分不清来源。
    if category == "UNIVIEW":
        return "UNIVIEW", subtype
    if subtype == "ANR":
        return "AEE", "ANR"
    if category == "VENDOR_AEE":
        return "VENDOR_AEE", subtype
    return "AEE", subtype


def _normalize_dashboard_subtype(
    raw_event_type: str,
    event_type: str,
    category: str,
    *,
    path_on_device: str = "",
    artifact_uri: Optional[str] = None,
    nfs_path: str = "",
) -> str:
    normalized = normalize_aee_subtype(raw_event_type, event_type, category=category)
    if normalized != "其他":
        return normalized

    entry_dir = _resolve_dashboard_local_aee_dir(nfs_path=nfs_path, artifact_uri=artifact_uri)
    if entry_dir is not None:
        exp_main_summary = parse_exp_main_summary(entry_dir)
        exp_main_subtype = str(exp_main_summary.get("event_subtype") or "").strip()
        if exp_main_subtype:
            return exp_main_subtype

    return infer_aee_subtype_from_paths(path_on_device, nfs_path, artifact_uri or "") or normalized


def _infer_dashboard_package_name(
    extra: dict[str, Any],
    *,
    artifact_uri: Optional[str] = None,
) -> str:
    package_name = normalize_package_name(str(extra.get("package_name") or ""))
    if package_name:
        return package_name

    entry_dir = _resolve_dashboard_local_aee_dir(
        nfs_path=str(extra.get("nfs_path") or "").strip(),
        artifact_uri=artifact_uri,
    )
    if entry_dir is not None:
        exp_main_summary = parse_exp_main_summary(entry_dir)
        for key in ("package_name", "current_process"):
            candidate = normalize_package_name(exp_main_summary.get(key, ""))
            if candidate:
                return candidate

    return "unknown"


def _resolve_dashboard_local_aee_dir(
    *,
    nfs_path: str = "",
    artifact_uri: Optional[str] = None,
) -> Optional[Path]:
    for raw_path in (nfs_path, artifact_uri or ""):
        candidate = (raw_path or "").strip()
        if not candidate:
            continue
        try:
            resolved = resolve_local_artifact_path(candidate, must_exist=False)
        except ArtifactPathError:
            continue
        if resolved.exists():
            return resolved if resolved.is_dir() else resolved.parent
        if resolved.suffix:
            return resolved.parent
        return resolved
    return None


def _build_dashboard_section(events: list[dict[str, Any]]) -> AeeDashboardSectionOut:
    if not events:
        return _empty_dashboard_section()

    subtype_counts: dict[tuple[str, str], int] = defaultdict(int)
    package_stats: dict[str, dict[str, Any]] = {}
    for event in events:
        subtype_counts[(event["group"], event["subtype"])] += 1
        pkg = package_stats.setdefault(
            event["package_name"],
            {
                "total_count": 0,
                "devices": set(),
                "latest_detected_at": None,
                "subtype_breakdown": defaultdict(int),
            },
        )
        pkg["total_count"] += 1
        pkg["devices"].add(event["device_serial"])
        # #1956：按 (group, subtype) 计数——同名 subtype 可能分属不同平台分组（如 ANR）。
        pkg["subtype_breakdown"][(event["group"], event["subtype"])] += 1
        latest_ts = pkg["latest_detected_at"]
        if latest_ts is None or event["detected_at"] > latest_ts:
            pkg["latest_detected_at"] = event["detected_at"]

    subtype_distribution = [
        SubtypeDistributionOut(
            subtype=subtype,
            group=group,
            count=count,
            share=round(count / len(events), 4),
        )
        for (group, subtype), count in sorted(
            subtype_counts.items(),
            key=lambda item: (
                -item[1],
                _subtype_order_index(item[0][1]),
                item[0][0],
                item[0][1],
            ),
        )
    ]

    package_ranking = [
        PackageRankingOut(
            package_name=package_name,
            total_count=stats["total_count"],
            affected_device_count=len(stats["devices"]),
            latest_detected_at=_iso(stats["latest_detected_at"]),
            subtype_breakdown=[
                PackageSubtypeCountOut(subtype=subtype, group=group, count=count)
                for (group, subtype), count in sorted(
                    stats["subtype_breakdown"].items(),
                    key=lambda item: (
                        -item[1],
                        _subtype_order_index(item[0][1]),
                        item[0][0],
                        item[0][1],
                    ),
                )
            ],
        )
        for package_name, stats in sorted(
            package_stats.items(),
            key=lambda item: (
                -item[1]["total_count"],
                item[0] == "unknown",
                -(item[1]["latest_detected_at"] or datetime.min.replace(tzinfo=timezone.utc)).timestamp(),
                item[0],
            ),
        )
    ]

    return AeeDashboardSectionOut(
        total_events=len(events),
        affected_device_count=len({event["device_serial"] for event in events}),
        top_package_name=package_ranking[0].package_name if package_ranking else None,
        top_subtype=subtype_distribution[0].subtype if subtype_distribution else None,
        subtype_distribution=subtype_distribution,
        package_ranking=package_ranking,
    )


def _subtype_order_index(subtype: str) -> int:
    try:
        return _SUBTYPE_FIXED_ORDER.index(subtype)
    except ValueError:
        return len(_SUBTYPE_FIXED_ORDER)


def _aggregate_aee_breakdown(
    db: Session,
    *,
    job_ids: list[int],
    cur_start: datetime,
    now: datetime,
) -> AeeBreakdownOut:
    """按 package_name 聚合 AEE/VENDOR_AEE 崩溃与 ANR;reconciler signal
    携带 extra.nfs_path 时按 nfs_path 去重(同目录视为同 crash),ANR 用
    path_on_device 兜底以兼容旧 inotifyd 路径无 extra 的 signal。

    返回零值 AeeBreakdownOut 而非 None — 调用方决定是否上抛 None(早返回路径)。
    """
    sql = text("""
        SELECT
            COALESCE(NULLIF(extra->>'package_name', ''), 'unknown') AS pkg,
            COUNT(DISTINCT extra->>'nfs_path') FILTER (
                WHERE category = 'AEE'
                  AND COALESCE(NULLIF(extra->>'event_type', ''), 'CRASH') <> 'ANR'
            ) AS crash_count,
            COUNT(DISTINCT extra->>'nfs_path') FILTER (
                WHERE category = 'VENDOR_AEE'
                  AND COALESCE(NULLIF(extra->>'event_type', ''), 'CRASH') <> 'ANR'
            ) AS vendor_crash_count,
            COUNT(DISTINCT path_on_device) FILTER (
                WHERE category = 'ANR'
                   OR extra->>'event_type' = 'ANR'
            ) AS anr_count,
            MAX(detected_at) AS latest_detected_at
        FROM job_log_signal
        WHERE job_id = ANY(:job_ids)
          AND detected_at >= :cur_start
          AND detected_at <= :now
          AND (
              category IN ('AEE', 'VENDOR_AEE', 'ANR')
              OR extra->>'event_type' = 'ANR'
          )
        GROUP BY pkg
        ORDER BY (
            COUNT(DISTINCT extra->>'nfs_path') FILTER (
                WHERE category = 'AEE'
                  AND COALESCE(NULLIF(extra->>'event_type', ''), 'CRASH') <> 'ANR'
            )
            + COUNT(DISTINCT extra->>'nfs_path') FILTER (
                WHERE category = 'VENDOR_AEE'
                  AND COALESCE(NULLIF(extra->>'event_type', ''), 'CRASH') <> 'ANR'
            )
            + COUNT(DISTINCT path_on_device) FILTER (
                WHERE category = 'ANR'
                   OR extra->>'event_type' = 'ANR'
            )
        ) DESC, pkg ASC
    """)

    rows = db.execute(
        sql, {"job_ids": list(job_ids), "cur_start": cur_start, "now": now},
    ).all()

    by_package: list[PackageStatOut] = []
    crash_total = 0
    vendor_crash_total = 0
    anr_total = 0
    for pkg, crash, vendor_crash, anr, latest_ts in rows:
        # 排除三类计数全 0 的行(理论上 WHERE 已过滤,防御性兜底)
        if not (crash or vendor_crash or anr):
            continue
        by_package.append(PackageStatOut(
            package_name=pkg,
            crash_count=int(crash or 0),
            vendor_crash_count=int(vendor_crash or 0),
            anr_count=int(anr or 0),
            latest_detected_at=_iso(latest_ts),
        ))
        crash_total += int(crash or 0)
        vendor_crash_total += int(vendor_crash or 0)
        anr_total += int(anr or 0)

    return AeeBreakdownOut(
        crash_count=crash_total,
        vendor_crash_count=vendor_crash_total,
        anr_count=anr_total,
        packages=[p.package_name for p in by_package],
        by_package=by_package,
    )


@router.get("/plan-runs/{run_id}/report/export")
def export_plan_run_report(
    run_id: int,
    format: str = Query("markdown"),
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    """Export PlanRun summary + devices + timeline (bounded to avoid OOM)."""
    pr = db.get(PlanRun, run_id)
    if pr is None:
        raise HTTPException(status_code=404, detail="plan run not found")

    data = build_plan_run_export(db, pr)
    fmt = format.strip().lower()
    if fmt == "json":
        return JSONResponse(
            content=data,
            headers={
                "Content-Disposition": (
                    f'attachment; filename="plan-run-{run_id}-report.json"'
                ),
            },
        )
    if fmt != "markdown":
        raise HTTPException(status_code=400, detail="format must be markdown or json")
    markdown = plan_run_export_to_markdown(data)
    return PlainTextResponse(
        markdown,
        headers={
            "Content-Disposition": (
                f'attachment; filename="plan-run-{run_id}-report.md"'
            ),
        },
    )


@router.get("/plan-runs/{run_id}/summary", response_model=ApiResponse[dict])
def get_plan_run_summary(
    run_id: int,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
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
    """产物下载（**UI 权威入口**）：唯一带 run↔job 配对校验的一条。

    #2420 第 4 项：实现与 job 域脚本入口
    ``GET /runs/{job_id}/artifacts/{artifact_id}/download`` 共用
    ``backend/services/job_artifact_download.py``，守卫与判据两侧同形。
    """
    return build_artifact_download_response(
        db, job_id=job_id, artifact_id=artifact_id, plan_run_id=run_id,
    )
