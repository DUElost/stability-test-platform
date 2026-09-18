"""Job run endpoints: report, JIRA draft, steps, artifacts.

Extracted from tasks.py (Wave 8) — these are independent endpoints
that operate on JobInstance records, not part of the legacy compatibility layer.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.api.schemas import JiraDraftListItemOut, RunReportOut, RunStepOut
from backend.api.routes.auth import get_current_active_user, User
from backend.api.response import ApiResponse, ok
from backend.core.database import get_db
from backend.services.report_service import compose_run_report, build_jira_draft, _model_to_dict
from backend.services.job_artifact_download import build_artifact_download_response

router = APIRouter(prefix="/api/v1", tags=["runs"])
logger = logging.getLogger(__name__)


def _report_to_markdown(report: RunReportOut) -> str:
    risk = report.risk_summary if isinstance(report.risk_summary, dict) else {}
    counts = risk.get("counts") if isinstance(risk, dict) else {}
    if not isinstance(counts, dict):
        counts = {}
    lines = [
        f"# Run Report - {report.run.id}",
        "",
        f"- Generated: {report.generated_at.isoformat()}",
        f"- Task: {report.task.name} ({report.task.type})",
        f"- Run Status: {report.run.status}",
        f"- Device: {report.device.serial if report.device else 'N/A'}",
        f"- Host: {report.host.name if report.host else 'N/A'}",
        "",
        # #2419：「Summary Metrics」小节与下面的 restart_count 行已删——它们读的是
        # RUN_COMPLETE 快照里的 `log_summary`，而该字段自 ADR-0025 起**没有任何生产者**
        # （Agent 侧恒为 None），生产导出里永远是 `- N/A` / `0`：噪声且像数据。
        "## Risk Summary",
        f"- risk_level: {risk.get('risk_level', 'UNKNOWN') if isinstance(risk, dict) else 'UNKNOWN'}",
        f"- events_total: {counts.get('events_total', 0)}",
        f"- aee_entries: {counts.get('aee_entries', 0)}",
        "",
        "## Alerts",
    ]
    if report.alerts:
        for item in report.alerts:
            lines.append(f"- [{item.severity}] {item.code}: {item.message}")
    else:
        lines.append("- No alerts")

    lines.extend(["", "## Artifacts"])
    if report.run.artifacts:
        for item in report.run.artifacts:
            lines.append(
                f"- id={item.id}, uri={item.storage_uri}, size={item.size_bytes}, checksum={item.checksum}"
            )
    else:
        lines.append("- N/A")
    return "\n".join(lines)


# ── Report ────────────────────────────────────────────────────────────────────


def _require_job_in_plan_run(db: Session, job_id: int, plan_run_id: int | None) -> None:
    """#2420 第 2 项：`plan_run_id` 一旦给出，就必须与 job 的实际归属配对。

    `/runs/{run_id}/report*` 里的 `run_id` 一直是 **JobInstance.id**（`RecentRun.run_id`
    同源、`compose_run_report` 也是 `db.get(JobInstance, run_id)`），但路径上从不校验
    「这个 job 真属于你正在看的那个 run」：同类的
    `/plan-runs/{run}/jobs/{job}/artifacts` 是校验配对的（dev 实测错配 → 404），
    而报告端点对"这个 job 属于哪个 run"毫不把关 —— 于是从 PlanRun 详情点进一个已
    被保留清理删除、或本就写错的 id 时，会返回**另一个 run 的 job 报告**，页面标题
    又只写 `Job #N`，用户无从发现。

    不传该参数时保持原行为（老脚本与既有深链不破坏）。
    """
    if plan_run_id is None:
        return
    from backend.models.job import JobInstance

    job = db.get(JobInstance, job_id)
    if job is None or job.plan_run_id != plan_run_id:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "job_not_in_plan_run",
                "message": (
                    f"job {job_id} 不属于 plan_run {plan_run_id}"
                    "（可能已被保留清理删除，或链接本身写错）"
                ),
            },
        )


@router.get("/runs/{run_id}/report", response_model=ApiResponse[RunReportOut])
def get_run_report(
    run_id: int,
    plan_run_id: Optional[int] = Query(
        None, description="可选归属校验：给出则必须与 job 的 plan_run 配对（#2420）",
    ),
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    """实时口径报告（**脚本对外入口**）：每次现算，不走快照。

    #2420 第 3 项：信封与 ``/report/cached`` 统一为 ``{data, error}``
    （此前本端点返回裸对象、cached 返回信封，同一资源两套形状——照抄 UI
    代码的脚本拿到的是解不开的裸对象）。**UI 用 cached**（快照 + ``cached_at``
    标注，#1082 裁决；前端 ``runs.ts`` 只消费 ``*/cached``），
    **脚本要最新口径用本端点**（现在与 cached 同信封，解包代码可共用）。
    """
    _require_job_in_plan_run(db, run_id, plan_run_id)
    report = compose_run_report(db, run_id)
    if report is None:
        raise HTTPException(status_code=404, detail="run not found")
    return ok(report)


@router.get("/runs/{run_id}/report/export")
def export_run_report(
    run_id: int,
    format: str = Query("markdown"),
    plan_run_id: Optional[int] = Query(
        None, description="可选归属校验：给出则必须与 job 的 plan_run 配对（#2420）",
    ),
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    _require_job_in_plan_run(db, run_id, plan_run_id)
    report = compose_run_report(db, run_id)
    if report is None:
        raise HTTPException(status_code=404, detail="run not found")
    fmt = format.strip().lower()
    if fmt == "json":
        return JSONResponse(content=jsonable_encoder(_model_to_dict(report)))
    if fmt != "markdown":
        raise HTTPException(status_code=400, detail="format must be markdown or json")
    markdown = _report_to_markdown(report)
    return PlainTextResponse(
        markdown,
        headers={"Content-Disposition": f'attachment; filename="run-{run_id}-report.md"'},
    )


@router.get("/runs/{run_id}/report/cached")
def get_cached_run_report(
    run_id: int,
    plan_run_id: Optional[int] = Query(
        None, description="可选归属校验：给出则必须与 job 的 plan_run 配对（#2420）",
    ),
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    """Return cached report if post-processed, otherwise compute live.

    #1082 裁决语义：缓存 = 「该 Job 完成时刻」的报告快照；PlanRun 终态时由
    post_completion.refresh_report_cache_for_plan_run 批量重算刷新一次——此后
    快照即最终结果。快照生成时刻经响应体 ``cached_at`` 暴露（= post_processed_at），
    UI 据此标注「截至 xx 时刻」；需要最新口径的调用方走 /runs/{id}/report。
    """
    _require_job_in_plan_run(db, run_id, plan_run_id)
    from backend.models.job import JobInstance
    job = db.get(JobInstance, run_id)
    if job and job.post_processed_at and job.report_json:
        payload = dict(job.report_json)
        payload["cached_at"] = job.post_processed_at.isoformat()
        return ok(payload)

    report = compose_run_report(db, run_id)
    if report is None:
        raise HTTPException(status_code=404, detail="run not found")
    return ok(_model_to_dict(report))


# ── JIRA Draft ────────────────────────────────────────────────────────────────
# #290 出口分层（ADR-0012）：draft 是 per-Job 预览（本节只读端点）；唯一交付
# 路径是 extract 材料包 + /api/v1/jira JiraRun，不存在 draft→工单自动桥。


def _resolve_draft_project_key(db: Session, run_id: int) -> Optional[str]:
    """ADR-0029 P0：草稿端点解析 plan_run.project_id 快照 → test_project 键。

    run_id 是 JobInstance id（compose_run_report 同口径）；无 plan_run /
    未归属 / 未配置 jira_project_key → None（回落模板/全局默认，由
    build_jira_draft 标注来源）。
    """
    from backend.models.job import JobInstance
    from backend.services.jira_project_key import resolve_jira_project_key

    job = db.get(JobInstance, run_id)
    if job is None or job.plan_run_id is None:
        return None
    return resolve_jira_project_key(db, job.plan_run_id)


@router.get("/runs/{run_id}/jira-draft/cached")
def get_cached_jira_draft(
    run_id: int,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    """Return cached JIRA draft if post-processed, otherwise compute live."""
    from backend.models.job import JobInstance
    job = db.get(JobInstance, run_id)
    if job and job.post_processed_at and job.jira_draft_json:
        return ok(job.jira_draft_json)

    report = compose_run_report(db, run_id)
    if report is None:
        raise HTTPException(status_code=404, detail="run not found")
    draft = build_jira_draft(
        report, project_key_override=_resolve_draft_project_key(db, run_id),
    )
    return ok(_model_to_dict(draft))


@router.get(
    "/runs/jira-drafts",
    response_model=ApiResponse[List[JiraDraftListItemOut]],
)
def list_recent_jira_drafts(
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    """最近带缓存 JIRA 草稿的 Job 列表（#1532）。

    草稿是按完成 Job 稀疏生成的。若由调用方「取 N 条 PlanRun → 逐 Run 列 jobs
    → 逐 Job 取草稿」，在无草稿的 Run 上内层短路不触发，会退化成
    ``1 + N + Σ(每个 Run 的全部 Job)`` 次串行 404（单体 Run 可达设备数量级）。
    本端点一次查询给出 Job 域草稿及其 PlanRun 归属，调用方无需自算扇出。

    「已落草稿」判据在 SQL 侧下推为 ``jsonb_typeof(jira_draft_json) == 'object'``
    （#1696）：SQL NULL 与 JSON ``null``（``none_as_null=False`` 下显式 None 的
    落库形态）的 typeof 都不是 ``object``，一个判据同时排除两者。此前以
    ``post_processed_at IS NOT NULL`` 作判据、Python 侧再过滤取值，但草稿
    生成在 post_completion 中是 try/except、时间戳无条件写（报告缓存刷新也
    只动时间戳）——「有时间戳无草稿」的行真实存在，先 LIMIT 后过滤会让它们
    吃掉名额、静默少返回。
    """
    from backend.models.job import JobInstance

    jobs = db.execute(
        select(JobInstance)
        .where(JobInstance.post_processed_at.is_not(None))
        .where(func.jsonb_typeof(JobInstance.jira_draft_json) == "object")
        .order_by(JobInstance.post_processed_at.desc())
        .limit(limit)
    ).scalars().all()

    return ok([
        JiraDraftListItemOut(
            job_id=job.id,
            plan_run_id=job.plan_run_id,
            draft=job.jira_draft_json,
            ended_at=job.ended_at,
            post_processed_at=job.post_processed_at,
        )
        for job in jobs
    ])


# ── Steps ─────────────────────────────────────────────────────────────────────


@router.get("/runs/{run_id}/steps", response_model=List[RunStepOut])
def list_run_steps(
    run_id: int,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    """Return step status for a JobInstance, mapped from StepTrace records."""
    from backend.models.job import JobInstance, StepTrace as _StepTrace

    job = db.get(JobInstance, run_id)
    if not job:
        raise HTTPException(status_code=404, detail="run not found")

    traces = (
        db.query(_StepTrace)
        .filter(_StepTrace.job_id == run_id)
        .order_by(_StepTrace.original_ts)
        .all()
    )

    if traces:
        step_latest: dict = {}
        for t in traces:
            step_latest[t.step_id] = t

        result = []
        for idx, (step_id, t) in enumerate(step_latest.items()):
            is_terminal = t.status in ("COMPLETED", "FAILED", "SKIPPED")
            result.append(RunStepOut(
                id=t.id,
                run_id=t.job_id,
                phase=t.stage,
                step_order=idx,
                name=step_id,
                action="",
                params={},
                status=t.status,
                started_at=t.original_ts,
                finished_at=t.created_at if is_terminal else None,
                exit_code=t.exit_code,
                error_message=t.error_message,
                log_line_count=0,
                created_at=t.created_at,
            ))
        return result

    pipeline_def = job.pipeline_def or {}
    lifecycle = pipeline_def.get("lifecycle", {})
    now = datetime.now(timezone.utc)
    result = []
    idx = 0
    phase_steps = [
        ("init", lifecycle.get("init", [])),
        (
            "patrol",
            (lifecycle.get("patrol") or {}).get("steps", [])
            if isinstance(lifecycle.get("patrol"), dict)
            else [],
        ),
        ("teardown", lifecycle.get("teardown", [])),
    ]
    for phase_name, steps in phase_steps:
        for step in (steps or []):
            result.append(RunStepOut(
                id=idx,
                run_id=run_id,
                phase=phase_name,
                step_order=idx,
                name=step.get("step_id", f"step_{idx}"),
                action=step.get("action", ""),
                params=step.get("params", {}),
                status="PENDING",
                started_at=None,
                finished_at=None,
                exit_code=None,
                error_message=None,
                log_line_count=0,
                created_at=now,
            ))
            idx += 1
    return result


@router.get("/runs/{run_id}/steps/{step_id}", response_model=RunStepOut)
def get_run_step(
    run_id: int,
    step_id: int,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    """Get a single step detail from StepTrace."""
    from backend.models.job import StepTrace as _StepTrace

    trace = db.query(_StepTrace).filter(_StepTrace.id == step_id, _StepTrace.job_id == run_id).first()
    if not trace:
        raise HTTPException(status_code=404, detail="step not found")
    is_terminal = trace.status in ("COMPLETED", "FAILED", "SKIPPED")
    return RunStepOut(
        id=trace.id,
        run_id=trace.job_id,
        phase=trace.stage,
        step_order=0,
        name=trace.step_id,
        action="",
        params={},
        status=trace.status,
        started_at=trace.original_ts,
        finished_at=trace.created_at if is_terminal else None,
        exit_code=trace.exit_code,
        error_message=trace.error_message,
        log_line_count=0,
        created_at=trace.created_at,
    )


# ── Artifacts ─────────────────────────────────────────────────────────────────


@router.get("/runs/{run_id}/artifacts/{artifact_id}/download")
def download_run_artifact(
    run_id: int,
    artifact_id: int,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    """产物下载（**脚本对外入口，job 域**）：路径参数是 JobInstance.id。

    #2420 第 4 项：同一资源的两条路由收敛为一份实现
    （``backend/services/job_artifact_download.py``），校验顺序与 ``run_log_bundle``
    409 守卫两侧同形。UI 权威入口是带 run 配对的
    ``GET /plan-runs/{run_id}/jobs/{job_id}/artifacts/{artifact_id}/download``；
    本条留给只持有 job id 的调用方（``/results`` 与报告 DTO 里的 id 都是 job 域）。
    """
    return build_artifact_download_response(db, job_id=run_id, artifact_id=artifact_id)
