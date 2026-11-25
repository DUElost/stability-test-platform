"""PlanRun 单 Job 详情与延迟加载的有上限子查询（#83 / #4117 阶段 B2-3）。

契约（``docs/design/2026-11-24-list-pagination-and-facets-contract.md`` §5.3）：
- ``GET /api/v1/plan-runs/{run_id}/jobs/{job_id}``：校验 ``(run_id, job_id)`` 归属，
  错配返回 404；返回单机完整详情，不含全段日志或全 PlanRun traces；
- ``GET /api/v1/plan-runs/{run_id}/jobs/{job_id}/timeline``：按 Job 延迟加载有上限的
  阶段/步骤时间线（截断长错误信息、不返回 ``output`` 原文）；
- ``GET /api/v1/plan-runs/{run_id}/jobs/{job_id}/watcher-signals``：按 Job 延迟加载有上限的
  Watcher 逐条异常明细（不含 ``first_lines`` 原始日志段）。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from backend.api.schemas.plan_run import (
    PlanRunJobDetailOut,
    PlanRunJobTimelineOut,
    PlanRunJobTraceItemOut,
    PlanRunJobWatcherSignalItemOut,
    PlanRunJobWatcherSignalsOut,
)
from backend.models.device_lease import DeviceLease
from backend.models.enums import LeaseStatus, LeaseType
from backend.models.host import Device, Host
from backend.models.job import JobArtifact, JobInstance, JobLogSignal, StepTrace
from backend.models.plan_run import PlanRun, PlanRunHost
from backend.services.errors import NotFound
from backend.services.plan_run_devices import build_device_matrix_item
from backend.services.plan_run_read_common import (
    aware,
    iso,
    resolve_step_script,
    snapshot_step_scripts,
)

_MAX_TRACE_ERROR_CHARS = 512


def _require_job_in_plan_run(
    db: Session,
    run_id: int,
    job_id: int,
) -> tuple[PlanRun, JobInstance]:
    pr = db.get(PlanRun, run_id)
    if pr is None:
        raise NotFound("plan run not found")
    job = db.get(JobInstance, job_id)
    if job is None or int(job.plan_run_id) != int(run_id):
        raise NotFound("job not found in this plan run")
    return pr, job


def build_plan_run_job_detail(
    db: Session,
    run_id: int,
    job_id: int,
    *,
    now: Optional[datetime] = None,
) -> PlanRunJobDetailOut:
    """只读构造单 Job 完整详情（严格校验 ``(run_id, job_id)`` 归属）。"""
    pr, job = _require_job_in_plan_run(db, run_id, job_id)
    ref_now = now or datetime.now(timezone.utc)

    row = db.execute(
        select(
            Device,
            Host.status.label("host_status"),
            Host.name.label("host_name"),
            Host.ip.label("host_ip"),
            DeviceLease.job_id.label("lease_job_id"),
        )
        .select_from(JobInstance)
        .outerjoin(Device, Device.id == JobInstance.device_id)
        .outerjoin(Host, Host.id == JobInstance.host_id)
        .outerjoin(
            DeviceLease,
            and_(
                DeviceLease.device_id == JobInstance.device_id,
                DeviceLease.status == LeaseStatus.ACTIVE.value,
                DeviceLease.lease_type == LeaseType.JOB.value,
            ),
        )
        .where(JobInstance.id == job.id)
    ).one()

    coord_map: dict[str, datetime] = {}
    if job.host_id:
        hb = db.execute(
            select(PlanRunHost.coordinator_heartbeat_at).where(
                PlanRunHost.plan_run_id == run_id,
                PlanRunHost.host_id == job.host_id,
            )
        ).scalar_one_or_none()
        if hb is not None:
            coord_map[job.host_id] = aware(hb)

    script_index = snapshot_step_scripts(pr.plan_snapshot)
    matrix_item = build_device_matrix_item(
        job,
        row.Device,
        row.host_status,
        row.lease_job_id,
        coord_heartbeat_by_host=coord_map,
        script_index=script_index,
        db=db,
        now=ref_now,
    )

    step_trace_count = int(
        db.execute(
            select(func.count(StepTrace.id)).where(StepTrace.job_id == job.id)
        ).scalar_one()
        or 0
    )
    artifact_count = int(
        db.execute(
            select(func.count(JobArtifact.id)).where(JobArtifact.job_id == job.id)
        ).scalar_one()
        or 0
    )
    host_display = (
        (row.host_name or "").strip()
        or (row.host_ip or "").strip()
        or job.host_id
    )
    return PlanRunJobDetailOut(
        **matrix_item.model_dump(),
        plan_run_id=int(run_id),
        host_name=host_display,
        watcher_capability=job.watcher_capability,
        execution_state=job.execution_state,
        last_execution_heartbeat_at=iso(job.last_execution_heartbeat_at),
        last_progress_at=iso(job.last_progress_at),
        step_trace_count=step_trace_count,
        artifact_count=artifact_count,
    )


def build_plan_run_job_timeline(
    db: Session,
    run_id: int,
    job_id: int,
    *,
    skip: int = 0,
    limit: int = 100,
) -> PlanRunJobTimelineOut:
    """按 Job 延迟加载有上限阶段时间线（不返回整段 ``output``）。"""
    pr, job = _require_job_in_plan_run(db, run_id, job_id)
    script_index = snapshot_step_scripts(pr.plan_snapshot)

    total = int(
        db.execute(
            select(func.count(StepTrace.id)).where(StepTrace.job_id == job.id)
        ).scalar_one()
        or 0
    )
    rows = db.execute(
        select(
            StepTrace.id,
            StepTrace.stage,
            StepTrace.step_id,
            StepTrace.event_type,
            StepTrace.status,
            StepTrace.exit_code,
            StepTrace.error_message,
            StepTrace.original_ts,
            StepTrace.created_at,
        )
        .where(StepTrace.job_id == job.id)
        .order_by(StepTrace.original_ts.asc(), StepTrace.id.asc())
        .offset(skip)
        .limit(limit)
    ).all()

    items: list[PlanRunJobTraceItemOut] = []
    for row in rows:
        err = row.error_message
        if err and len(err) > _MAX_TRACE_ERROR_CHARS:
            err = err[:_MAX_TRACE_ERROR_CHARS] + "…"
        script_name, script_version = resolve_step_script(
            script_index, stage=row.stage, step_key=row.step_id,
        )
        items.append(
            PlanRunJobTraceItemOut(
                id=int(row.id),
                stage=str(row.stage),
                step_id=str(row.step_id),
                event_type=str(row.event_type),
                status=str(row.status),
                exit_code=row.exit_code,
                error_message=err,
                script_name=script_name,
                script_version=script_version,
                original_ts=iso(row.original_ts) or "",
                created_at=iso(row.created_at) or "",
            )
        )

    return PlanRunJobTimelineOut(
        plan_run_id=int(run_id),
        job_id=int(job.id),
        total=total,
        skip=skip,
        limit=limit,
        items=items,
    )


def build_plan_run_job_watcher_signals(
    db: Session,
    run_id: int,
    job_id: int,
    *,
    skip: int = 0,
    limit: int = 100,
    category: Optional[str] = None,
) -> PlanRunJobWatcherSignalsOut:
    """按 Job 延迟加载有上限 Watcher 异常明细（不含 ``first_lines`` 原始日志段）。"""
    _pr, job = _require_job_in_plan_run(db, run_id, job_id)

    conditions = [JobLogSignal.job_id == job.id]
    if category and category.strip() and category.strip().lower() != "all":
        conditions.append(JobLogSignal.category == category.strip().upper())

    total = int(
        db.execute(
            select(func.count(JobLogSignal.id)).where(*conditions)
        ).scalar_one()
        or 0
    )
    rows = db.execute(
        select(
            JobLogSignal.id,
            JobLogSignal.seq_no,
            JobLogSignal.category,
            JobLogSignal.source,
            JobLogSignal.detected_at,
            JobLogSignal.received_at,
            JobLogSignal.path_on_device,
            JobLogSignal.artifact_uri,
            JobLogSignal.device_log_event_id,
            JobLogSignal.size_bytes,
        )
        .where(*conditions)
        .order_by(JobLogSignal.detected_at.desc(), JobLogSignal.id.desc())
        .offset(skip)
        .limit(limit)
    ).all()

    items = [
        PlanRunJobWatcherSignalItemOut(
            id=int(row.id),
            seq_no=int(row.seq_no),
            category=str(row.category),
            source=str(row.source),
            detected_at=iso(row.detected_at) or "",
            received_at=iso(row.received_at) or "",
            path_on_device=str(row.path_on_device),
            artifact_uri=row.artifact_uri,
            device_log_event_id=str(row.device_log_event_id) if row.device_log_event_id is not None else None,
            size_bytes=int(row.size_bytes) if row.size_bytes is not None else None,
        )
        for row in rows
    ]

    return PlanRunJobWatcherSignalsOut(
        plan_run_id=int(run_id),
        job_id=int(job.id),
        total=total,
        skip=skip,
        limit=limit,
        items=items,
    )
