"""PlanRun API — ADR-0020.

Provides PlanRun list/detail/jobs/summary endpoints.
"""

from __future__ import annotations

import logging
import time
from typing import Optional


from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy.orm import Session

from backend.api.response import ApiResponse, ok
from backend.api.routes.auth import get_current_active_user, User
from backend.api.schemas.case_result import (
    TestCaseResultsPayload,
)
from backend.api.schemas.plan_run import (
    JobInstanceOut,
    JobManualActionIn,
    JobManualActionOut,
    PlanChainOut,
    PlanRunAbortIn,
    PlanRunDevicesOut,
    PlanRunEventsOut,
    PlanRunLogEventsOut,
    PlanRunDetailOut,
    PlanRunListPageOut,
    PlanRunTimelineOut,
    WatcherSummaryOut,
)
from backend.core.database import get_db
from backend.core.metrics import (
    record_plan_run_devices_query_duration,
)
from backend.models.enums import PlanRunStatus
from backend.services.plan_run_timeline import build_plan_run_timeline
from backend.services.plan_run_event_feed import build_plan_run_events
from backend.services.plan_run_chain import build_plan_run_chain
from backend.services.plan_run_archive import archive_plan_run_logs
from backend.services.plan_run_summary import build_plan_run_summary
from backend.services.plan_run_job_artifacts import list_plan_run_job_artifacts
from backend.services.plan_run_result_views import (
    build_plan_run_log_events,
    build_plan_run_test_case_results,
)
from backend.services.plan_run_devices import (
    build_plan_run_devices,
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
from backend.services.job_artifact_download import build_artifact_download_response
from backend.services.plan_run_catalog import (
    build_plan_run_detail,
    build_plan_run_jobs,
    build_plan_run_list_page,
)
from backend.services.plan_run_watcher_summary import (
    _MAX_WATCHER_WINDOW_MIN,
    build_plan_run_crash_details,
    build_plan_run_watcher_summary,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["plan-runs"])
# ── Schemas ──────────────────────────────────────────────────────────────


# ── Helpers ──────────────────────────────────────────────────────────────

from backend.services.plan_run_read_common import iso as _iso
from backend.services.plan_run_read_common import require_plan_run as _require_plan_run


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
    """#1520 薄壳：列表装配在 `services/plan_run_catalog.build_plan_run_list_page`。"""
    return ok(build_plan_run_list_page(
        db,
        skip=skip,
        limit=limit,
        plan_id=plan_id,
        statuses=status,
        project_key=project_key,
        q=q,
    ))


@router.get("/plan-runs/{run_id}", response_model=ApiResponse[PlanRunDetailOut])
def get_plan_run(
    run_id: int,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    """#1520 薄壳：详情装配在 `services/plan_run_catalog.build_plan_run_detail`。"""
    return ok(build_plan_run_detail(db, run_id))


@router.get("/plan-runs/{run_id}/jobs", response_model=ApiResponse[list[JobInstanceOut]])
def list_plan_run_jobs(
    run_id: int,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    """#1520 薄壳：jobs 装配在 `services/plan_run_catalog.build_plan_run_jobs`。"""
    return ok(build_plan_run_jobs(db, run_id))


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
    return ok(await archive_plan_run_logs(
        db,
        run_id,
        allow_retired=current_user.role == "admin",
        user_id=current_user.id,
        username=current_user.username,
        request=request,
    ))


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




# ── ADR-0021/ADR-0022 C5a₂ 聚合端点（chain/timeline/events/devices/watcher）──
# 业务在对应 service；本文件只留 Query + `_require_plan_run` + `ok(build_*)`。

# ── 公共常量 ─────────────────────────────────────────────────────────────

_MAX_EVENTS_LIMIT             = 500
_DEFAULT_EVENTS_LIMIT         = 100


# ── Endpoint 1: GET /plan-runs/{id}/chain ────────────────────────────────


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
    return ok(build_plan_run_chain(db, pr))


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
    """#1520 薄壳：聚合逻辑在 `services/plan_run_watcher_summary`。"""
    pr = _require_plan_run(db, run_id)
    return ok(build_plan_run_watcher_summary(
        db, pr, window_minutes=window_minutes, time_scope=time_scope,
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
    return ok(build_plan_run_log_events(
        db, run_id, skip=skip, limit=limit, state=state, platform=platform,
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
    return ok(build_plan_run_test_case_results(
        db, run_id, skip=skip, limit=limit, status=status,
    ))


# M0/C-6: watcher_capability 降级严重度排序 — 取该 PlanRun 下 Job 中"最降级"的一档,
# 使得只要有任一设备落到 reconciler 单通道(unavailable)即可在前端给出提示。
# 数值越大越降级;未知能力按 0 处理(不触发降级徽章)。
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
    """#1520 薄壳：见 `services/plan_run_watcher_summary.build_plan_run_crash_details`。"""
    pr = _require_plan_run(db, run_id)
    return ok(build_plan_run_crash_details(
        db, pr, package_name=package_name, time_scope=time_scope,
    ))



@router.get("/plan-runs/{run_id}/report/export")
def export_plan_run_report(
    run_id: int,
    format: str = Query("markdown"),
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    """Export PlanRun summary + devices + timeline (bounded to avoid OOM)."""
    pr = _require_plan_run(db, run_id)

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
    return ok(build_plan_run_summary(db, run_id))


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
    return ok(list_plan_run_job_artifacts(db, run_id, job_id))


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
