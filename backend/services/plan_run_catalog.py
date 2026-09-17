"""PlanRun catalog slice (#1520 God-module): 列表 / 详情 / jobs 的只读装配层.

Covers ``GET /plan-runs``、``GET /plan-runs/{id}``、``GET /plan-runs/{id}/jobs``
三条只读端点的装配逻辑：DTO 装配（``plan_run_out`` 族）、list/count/stats 共享
过滤器与分页聚合。routes 只留 Query 声明与 ``ok(build_*)`` 薄壳；
``plan_run_out`` 等私有名仍从路由模块 re-export（既有测试从路由导入）。

``_iso``/``_aware`` 为过渡副本（先例：``plan_run_export``；在窗 chain 切片的
``plan_run_read_common`` 落地后统一收编——出口登记在本单 note 的 Revisit）。
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.orm import Session, joinedload

from backend.api.schemas.plan_run import (
    JobInstanceOut,
    PlanRunDetailOut,
    PlanRunListPageOut,
    PlanRunListStatsOut,
    StepTraceOut,
)
from backend.core.job_timeout_config import (
    PRECHECK_ACTIVE_STALE_SECONDS,
    PRECHECK_QUEUE_STALE_SECONDS,
)
from backend.models.enums import PlanRunStatus
from backend.models.host import Device
from backend.models.job import JobInstance, StepTrace
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun
from backend.models.project import TestProject


def _iso(v) -> str | None:
    if v is None:
        return None
    return v.isoformat()


def _aware(ts: datetime | None) -> datetime | None:
    if ts is None:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


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


def build_plan_run_list_page(
    db: Session,
    *,
    skip: int,
    limit: int,
    plan_id: Optional[int],
    statuses: Optional[list[PlanRunStatus]],
    project_key: Optional[str],
    q: Optional[str],
) -> PlanRunListPageOut:
    needle = (q or "").strip()
    need_plan_join = bool(needle)

    base = select(PlanRun).options(joinedload(PlanRun.project))
    base = _apply_plan_run_list_filters(
        base,
        plan_id=plan_id,
        statuses=statuses,
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
        statuses=statuses,
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
    return (
        PlanRunListPageOut(
            items=items,
            total=total,
            skip=skip,
            limit=limit,
            stats=stats,
        )
    )




def build_plan_run_detail(db: Session, run_id: int) -> PlanRunDetailOut:
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
    return (_plan_run_out(pr, jobs=[_job_out(j, []) for j in jobs], plan_name=plan_name))




def build_plan_run_jobs(db: Session, run_id: int) -> list[JobInstanceOut]:
    jobs = db.execute(
        select(JobInstance).where(JobInstance.plan_run_id == run_id)
    ).scalars().all()
    if not jobs:
        return ([])

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

    return ([
        _job_out(j, traces_by_job.get(j.id, []), devices.get(j.device_id))
        for j in jobs
    ])



