import asyncio
import json
import logging
import os
import shlex
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from urllib.parse import unquote, urlparse

import paramiko
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse
from pydantic import BaseModel, Field
from pydantic.fields import PrivateAttr
from sqlalchemy import text
from sqlalchemy.orm import Session, selectinload

from backend.core.database import get_db
from backend.core.task_templates import list_templates as list_core_templates
from backend.models.schemas import Device, DeviceStatus, Host, HostStatus, LogArtifact, RunStatus, RunStep, RunStepStatus, Task, TaskRun, TaskStatus
from backend.api.schemas import (
    AgentLogOut,
    AgentLogQuery,
    DeviceLiteOut,
    HostLiteOut,
    JiraDraftOut,
    LogArtifactIn,
    PaginatedResponse,
    RiskAlertOut,
    RunAgentOut,
    RunCompleteIn,
    RunOut,
    RunReportOut,
    RunStepOut,
    RunStepUpdate,
    RunUpdate,
    TaskCreate,
    TaskDispatch,
    TaskTemplateOut,
    TaskOut,
)
from backend.api.routes.auth import get_current_active_user, User, verify_agent_secret
from backend.core.audit import record_audit
from backend.services.report_service import (
    compose_run_report,
    build_jira_draft,
    build_risk_alerts as _build_risk_alerts,
    parse_run_log_summary as _parse_run_log_summary,
)
from backend.services.report_service import _load_risk_summary_from_artifacts, _model_to_dict
from backend.api.routes.websocket import broadcast_run_update, broadcast_task_update

router = APIRouter(prefix="/api/v1", tags=["tasks"])
logger = logging.getLogger(__name__)

DEVICE_LOCK_LEASE_SECONDS = int(os.getenv("DEVICE_LOCK_LEASE_SECONDS", "600"))

TASK_STATUS_TRANSITIONS = {
    TaskStatus.PENDING: {TaskStatus.QUEUED, TaskStatus.CANCELED},
    TaskStatus.QUEUED: {TaskStatus.RUNNING, TaskStatus.CANCELED, TaskStatus.FAILED},
    TaskStatus.RUNNING: {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELED},
    TaskStatus.COMPLETED: set(),
    TaskStatus.FAILED: set(),
    TaskStatus.CANCELED: set(),
}

RUN_STATUS_TRANSITIONS = {
    RunStatus.QUEUED: {RunStatus.DISPATCHED, RunStatus.FAILED, RunStatus.CANCELED},
    RunStatus.DISPATCHED: {RunStatus.RUNNING, RunStatus.FAILED, RunStatus.CANCELED},
    RunStatus.RUNNING: {RunStatus.FINISHED, RunStatus.FAILED, RunStatus.CANCELED},
    RunStatus.FINISHED: set(),
    RunStatus.FAILED: set(),
    RunStatus.CANCELED: set(),
}

RUN_STATUS_ALIASES = {
    "COMPLETED": RunStatus.FINISHED.value,
    "CANCELLED": RunStatus.CANCELED.value,
}


def _normalize_run_status(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    normalized = value.strip().upper()
    return RUN_STATUS_ALIASES.get(normalized, normalized)


def _artifact_download_target(storage_uri: str) -> Dict[str, str]:
    parsed = urlparse(storage_uri)
    scheme = parsed.scheme.lower()
    if scheme in {"http", "https"}:
        return {"kind": "redirect", "url": storage_uri}

    if scheme != "file":
        raise HTTPException(status_code=400, detail=f"unsupported artifact scheme: {scheme or 'empty'}")

    if parsed.netloc and parsed.path:
        local_path = Path(f"//{parsed.netloc}{unquote(parsed.path)}")
    elif parsed.netloc and not parsed.path:
        local_path = Path(unquote(parsed.netloc))
    else:
        local_path = Path(unquote(parsed.path))

    if not local_path.exists() or not local_path.is_file():
        raise HTTPException(status_code=404, detail=f"artifact file not found: {local_path}")
    return {"kind": "local", "path": str(local_path)}




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
        "## Summary Metrics",
    ]
    if report.summary_metrics:
        for key, value in report.summary_metrics.items():
            lines.append(f"- {key}: {value}")
    else:
        lines.append("- N/A")

    lines.extend(
        [
            "",
            "## Risk Summary",
            f"- risk_level: {risk.get('risk_level', 'UNKNOWN') if isinstance(risk, dict) else 'UNKNOWN'}",
            f"- events_total: {counts.get('events_total', 0)}",
            f"- restart_count: {counts.get('restart_count', 0)}",
            f"- aee_entries: {counts.get('aee_entries', 0)}",
            "",
            "## Alerts",
        ]
    )
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


def _ensure_task_transition(task: Task, target_status: TaskStatus) -> None:
    current_status = task.status
    if current_status == target_status:
        return
    allowed = TASK_STATUS_TRANSITIONS.get(current_status, set())
    if target_status not in allowed:
        raise HTTPException(
            status_code=409,
            detail=f"illegal task transition {current_status.value}->{target_status.value}",
        )


def _ensure_run_transition(run: TaskRun, target_status: RunStatus) -> None:
    current_status = run.status
    if current_status == target_status:
        return
    allowed = RUN_STATUS_TRANSITIONS.get(current_status, set())
    if target_status not in allowed:
        raise HTTPException(
            status_code=409,
            detail=f"illegal run transition {current_status.value}->{target_status.value}",
        )


def _update_distributed_task_status(db: Session, task_id: int) -> None:
    """更新分布式任务的整体状态"""
    task = db.get(Task, task_id)
    if not task:
        return

    runs = db.query(TaskRun).filter(TaskRun.task_id == task_id).all()

    # 检查所有 TaskRun 的状态
    all_finished = all(r.status in [RunStatus.FINISHED, RunStatus.FAILED] for r in runs)
    any_failed = any(r.status == RunStatus.FAILED for r in runs)
    any_running = any(r.status == RunStatus.RUNNING for r in runs)

    if any_failed:
        task.status = TaskStatus.FAILED
    elif all_finished:
        task.status = TaskStatus.COMPLETED
    elif any_running:
        task.status = TaskStatus.RUNNING
    else:
        task.status = TaskStatus.QUEUED

    db.commit()


def _create_run_steps_from_pipeline(db: Session, run_id: int, pipeline_def: dict) -> None:
    """Create PENDING RunStep records from a pipeline definition when a run first starts."""
    phases = pipeline_def.get("phases", [])
    for phase in phases:
        phase_name = phase.get("name", "unknown")
        steps = phase.get("steps", [])
        for idx, step in enumerate(steps):
            run_step = RunStep(
                run_id=run_id,
                phase=phase_name,
                step_order=idx,
                name=step.get("name", f"step_{idx}"),
                action=step.get("action", ""),
                params=step.get("params", {}),
                status=RunStepStatus.PENDING,
            )
            db.add(run_step)
    db.flush()


def _aggregate_run_status_from_steps(db: Session, run: TaskRun, pipeline_def: dict) -> None:
    """Derive TaskRun status from the aggregate state of its RunStep records."""
    steps = db.query(RunStep).filter(RunStep.run_id == run.id).all()
    if not steps:
        return

    all_completed = all(s.status == RunStepStatus.COMPLETED for s in steps)
    any_failed_stop = False
    any_failed = False

    # Build a lookup for step failure policies from pipeline_def
    step_policies = {}
    for phase in pipeline_def.get("phases", []):
        for step_def in phase.get("steps", []):
            step_policies[step_def.get("name", "")] = step_def.get("on_failure", "stop")

    for s in steps:
        if s.status == RunStepStatus.FAILED:
            any_failed = True
            policy = step_policies.get(s.name, "stop")
            if policy == "stop":
                any_failed_stop = True

    if any_failed_stop:
        run.status = RunStatus.FAILED
        run.finished_at = datetime.utcnow()
    elif all_completed:
        run.status = RunStatus.FINISHED
        run.finished_at = datetime.utcnow()
        if any_failed:
            run.log_summary = (run.log_summary or "") + " [WARNING: some steps failed with on_failure=continue]"


def _acquire_device_lock(db: Session, device_id: int, run_id: int) -> None:
    now = datetime.utcnow()
    expires_at = now + timedelta(seconds=DEVICE_LOCK_LEASE_SECONDS)
    result = db.execute(
        text(
            """
            UPDATE devices
            SET status = :busy_status,
                lock_run_id = :run_id,
                lock_expires_at = :expires_at
            WHERE id = :device_id
              AND status = :online_status
              AND (lock_run_id IS NULL OR lock_expires_at IS NULL OR lock_expires_at < :now)
            """
        ),
        {
            "device_id": device_id,
            "run_id": run_id,
            "expires_at": expires_at,
            "now": now,
            "busy_status": DeviceStatus.BUSY.value,
            "online_status": DeviceStatus.ONLINE.value,
        },
    )
    if result.rowcount != 1:
        raise HTTPException(status_code=409, detail="device busy")


def _extend_device_lock(db: Session, device_id: int, run_id: int) -> None:
    now = datetime.utcnow()
    expires_at = now + timedelta(seconds=DEVICE_LOCK_LEASE_SECONDS)
    db.execute(
        text(
            """
            UPDATE devices
            SET lock_expires_at = :expires_at
            WHERE id = :device_id AND lock_run_id = :run_id
            """
        ),
        {
            "device_id": device_id,
            "run_id": run_id,
            "expires_at": expires_at,
        },
    )


def _release_device_lock(db: Session, device_id: int, run_id: int) -> None:
    db.execute(
        text(
            """
            UPDATE devices
            SET status = CASE
                    WHEN status = :busy_status THEN :online_status
                    ELSE status
                END,
                lock_run_id = NULL,
                lock_expires_at = NULL
            WHERE id = :device_id AND lock_run_id = :run_id
            """
        ),
        {
            "device_id": device_id,
            "run_id": run_id,
            "busy_status": DeviceStatus.BUSY.value,
            "online_status": DeviceStatus.ONLINE.value,
        },
    )


@router.get("/tasks", response_model=Any)
def list_tasks(
    request: Request,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    status: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """获取任务列表（分页）"""
    query = db.query(Task).order_by(Task.id.desc())
    if status:
        query = query.filter(Task.status == status)
    total = query.count()
    rows = query.offset(skip).limit(limit).all()
    items: List[TaskOut] = []
    for row in rows:
        if hasattr(TaskOut, "model_validate"):
            items.append(TaskOut.model_validate(row))
        else:
            items.append(TaskOut.from_orm(row))
    # 兼容旧接口：未显式传分页参数时返回数组
    if "skip" not in request.query_params and "limit" not in request.query_params:
        return items
    return PaginatedResponse(items=items, total=total, skip=skip, limit=limit)


@router.get("/task-templates", response_model=List[TaskTemplateOut])
def list_task_templates():
    templates = list_core_templates()
    return [
        TaskTemplateOut(
            type=tpl.type,
            name=tpl.name,
            description=tpl.description,
            default_params=tpl.default_params,
            script_paths=tpl.script_paths,
        )
        for tpl in templates
    ]


@router.post("/tasks", response_model=TaskOut)
def create_task(payload: TaskCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user), request: Request = None):
    resolved_target_device_id = payload.target_device_id

    # 生成 group_id（分布式任务用）
    import uuid
    group_id = str(uuid.uuid4())[:8] if payload.is_distributed else None

    # 处理单设备场景
    if resolved_target_device_id is None and payload.device_serial:
        device = db.query(Device).filter(Device.serial == payload.device_serial).first()
        if not device:
            raise HTTPException(status_code=400, detail="target device serial not found")
        resolved_target_device_id = device.id

    # 验证设备（单设备场景）
    if resolved_target_device_id is not None:
        target_device = db.get(Device, resolved_target_device_id)
        if not target_device:
            raise HTTPException(status_code=400, detail="target device not found")
        if target_device.status != DeviceStatus.ONLINE:
            raise HTTPException(status_code=409, detail="target device is not online")
        if not target_device.host_id:
            raise HTTPException(status_code=409, detail="target device has no host binding")
        host = db.get(Host, target_device.host_id)
        if not host or host.status != HostStatus.ONLINE:
            raise HTTPException(status_code=409, detail="target device host is not online")

    # 验证设备列表（分布式场景）
    device_ids = payload.device_ids or []
    if payload.is_distributed and not device_ids:
        raise HTTPException(status_code=400, detail="分布式任务需要提供设备列表 device_ids")

    # 验证分布式任务的所有设备
    if payload.is_distributed and device_ids:
        for device_id in device_ids:
            device = db.get(Device, device_id)
            if not device:
                raise HTTPException(status_code=400, detail=f"设备 {device_id} 不存在")
            if device.status != DeviceStatus.ONLINE:
                raise HTTPException(status_code=409, detail=f"设备 {device_id} 不在线")
            if not device.host_id:
                raise HTTPException(status_code=409, detail=f"设备 {device_id} 未绑定主机")

    # Pipeline-only: require pipeline_def and disable tool management linkage
    if payload.pipeline_def is None:
        raise HTTPException(status_code=422, detail="pipeline_def is required")
    if payload.tool_id is not None or payload.tool_snapshot is not None:
        raise HTTPException(status_code=400, detail="tool management is disabled; use pipeline_def only")

    tool_snapshot = None

    # Validate pipeline_def
    pipeline_def = None
    from backend.core.pipeline_validator import validate_pipeline_def
    is_valid, errors = validate_pipeline_def(payload.pipeline_def)
    if not is_valid:
        raise HTTPException(status_code=422, detail=f"Invalid pipeline definition: {'; '.join(errors)}")
    pipeline_def = payload.pipeline_def

    task = Task(
        name=payload.name,
        type=payload.type,
        template_id=payload.template_id,
        tool_id=payload.tool_id,
        params=payload.params,
        tool_snapshot=tool_snapshot,
        target_device_id=resolved_target_device_id,
        status=TaskStatus.PENDING,
        priority=payload.priority,
        group_id=group_id,
        is_distributed=payload.is_distributed,
        pipeline_def=pipeline_def,
    )
    db.add(task)
    db.flush()
    record_audit(
        db,
        action="create",
        resource_type="task",
        resource_id=task.id,
        details={
            "name": payload.name,
            "type": payload.type,
            "tool_id": payload.tool_id,
            "target_device_id": resolved_target_device_id,
            "priority": payload.priority,
            "pipeline_def_present": payload.pipeline_def is not None,
        },
        user_id=current_user.id,
        username=current_user.username,
        request=request,
    )
    db.commit()
    db.refresh(task)

    # 分布式任务：自动为每个设备创建 TaskRun
    if payload.is_distributed and device_ids:
        for device_id in device_ids:
            device = db.get(Device, device_id)
            run = TaskRun(
                task_id=task.id,
                host_id=device.host_id,
                device_id=device_id,
                group_id=group_id,
                status=RunStatus.QUEUED,
            )
            db.add(run)
            # 锁定设备
            device.status = DeviceStatus.BUSY
            device.lock_run_id = None  # 将在调度时设置

        # 自动更新任务状态为 QUEUED
        task.status = TaskStatus.QUEUED
        db.commit()

    logger.info(
        "task_created",
        extra={
            "task_id": task.id,
            "type": task.type,
            "target_device_id": task.target_device_id,
            "priority": task.priority,
            "is_distributed": task.is_distributed,
            "group_id": task.group_id,
        },
    )
    return task


@router.get("/tasks/{task_id}", response_model=TaskOut)
def get_task(task_id: int, db: Session = Depends(get_db)):
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")

    # 计算关联的 TaskRun 数量
    runs_count = db.query(TaskRun).filter(TaskRun.task_id == task_id).count()

    return TaskOut(
        id=task.id,
        name=task.name,
        type=task.type,
        template_id=task.template_id,
        tool_id=task.tool_id,
        params=task.params,
        tool_snapshot=task.tool_snapshot,
        target_device_id=task.target_device_id,
        status=task.status.value,
        priority=task.priority,
        group_id=task.group_id,
        is_distributed=task.is_distributed,
        runs_count=runs_count,
        created_at=task.created_at,
    )


@router.get("/tasks/{task_id}/runs", response_model=Any)
def get_task_runs(
    task_id: int,
    request: Request,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """获取任务的所有运行记录（分页）"""
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    base = (
        db.query(TaskRun)
        .options(selectinload(TaskRun.artifacts))
        .filter(TaskRun.task_id == task_id)
        .order_by(TaskRun.id.desc())
    )
    total = base.count()
    runs = base.offset(skip).limit(limit).all()
    run_items: List[RunOut] = []
    for run in runs:
        if hasattr(RunOut, "model_validate"):
            run_out = RunOut.model_validate(run)
        else:
            run_out = RunOut.from_orm(run)
        run_out.risk_summary = _load_risk_summary_from_artifacts(run.artifacts)
        run_items.append(run_out)
    # 兼容旧接口：未显式传分页参数时返回数组
    if "skip" not in request.query_params and "limit" not in request.query_params:
        return run_items
    return PaginatedResponse(items=run_items, total=total, skip=skip, limit=limit)


@router.get("/runs/{run_id}/report", response_model=RunReportOut)
def get_run_report(run_id: int, db: Session = Depends(get_db)):
    report = compose_run_report(db, run_id)
    if report is None:
        raise HTTPException(status_code=404, detail="run not found")
    return report


@router.get("/runs/{run_id}/report/export")
def export_run_report(run_id: int, format: str = Query("markdown"), db: Session = Depends(get_db)):
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


@router.post("/runs/{run_id}/jira-draft", response_model=JiraDraftOut)
def create_run_jira_draft(run_id: int, db: Session = Depends(get_db)):
    report = compose_run_report(db, run_id)
    if report is None:
        raise HTTPException(status_code=404, detail="run not found")
    return build_jira_draft(report)


@router.get("/runs/{run_id}/report/cached")
def get_cached_run_report(run_id: int, db: Session = Depends(get_db)):
    """Return cached report if post-processed, otherwise compute live."""
    run = db.get(TaskRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="run not found")
    if run.post_processed_at and run.report_json:
        return JSONResponse(content=run.report_json)
    # Fallback to live computation
    report = compose_run_report(db, run_id)
    if report is None:
        raise HTTPException(status_code=404, detail="run not found")
    return JSONResponse(content=jsonable_encoder(_model_to_dict(report)))


@router.get("/runs/{run_id}/jira-draft/cached")
def get_cached_jira_draft(run_id: int, db: Session = Depends(get_db)):
    """Return cached JIRA draft if post-processed, otherwise compute live."""
    run = db.get(TaskRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="run not found")
    if run.post_processed_at and run.jira_draft_json:
        return JSONResponse(content=run.jira_draft_json)
    # Fallback to live computation
    report = compose_run_report(db, run_id)
    if report is None:
        raise HTTPException(status_code=404, detail="run not found")
    draft = build_jira_draft(report)
    return JSONResponse(content=jsonable_encoder(_model_to_dict(draft)))


@router.get("/tasks/{task_id}/runs/{run_id}/artifacts/{artifact_id}/download")
def download_run_artifact(task_id: int, run_id: int, artifact_id: int, db: Session = Depends(get_db)):
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")

    run = db.get(TaskRun, run_id)
    if not run or run.task_id != task_id:
        raise HTTPException(status_code=404, detail="run not found")

    artifact = db.get(LogArtifact, artifact_id)
    if not artifact or artifact.run_id != run_id:
        raise HTTPException(status_code=404, detail="artifact not found")

    target = _artifact_download_target(artifact.storage_uri)
    if target["kind"] == "redirect":
        return RedirectResponse(url=target["url"], status_code=307)

    local_path = Path(target["path"])
    media_type = "application/gzip" if local_path.suffixes[-2:] == [".tar", ".gz"] else None
    return FileResponse(path=str(local_path), filename=local_path.name, media_type=media_type)


@router.post("/tasks/{task_id}/dispatch", response_model=RunOut)
def dispatch_task(task_id: int, payload: TaskDispatch, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user), request: Request = None):
    host = db.get(Host, payload.host_id)
    device = db.get(Device, payload.device_id)
    if not host or not device:
        raise HTTPException(status_code=400, detail="host or device not found")

    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    if task.target_device_id and task.target_device_id != payload.device_id:
        raise HTTPException(status_code=409, detail="task target device mismatch")

    # Atomic status update to prevent concurrent dispatch
    updated = db.execute(
        text(
            "UPDATE tasks SET status = :queued WHERE id = :task_id AND status = :pending"
        ),
        {
            "task_id": task.id,
            "queued": TaskStatus.QUEUED.value,
            "pending": TaskStatus.PENDING.value,
        },
    )
    if updated.rowcount != 1:
        raise HTTPException(status_code=409, detail="task not pending")

    run = TaskRun(
        task_id=task.id,
        host_id=host.id,
        device_id=device.id,
        status=RunStatus.QUEUED,
    )
    db.add(run)
    db.flush()
    _acquire_device_lock(db, device.id, run.id)
    record_audit(
        db,
        action="dispatch",
        resource_type="task",
        resource_id=task.id,
        details={"host_id": host.id, "device_id": device.id, "run_id": run.id},
        user_id=current_user.id,
        username=current_user.username,
        request=request,
    )
    db.commit()
    db.refresh(run)
    return run


@router.get("/agent/runs/pending", response_model=List[RunAgentOut])
def agent_pending_runs(
    host_id: int = Query(...),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    _: bool = Depends(verify_agent_secret),
):
    runs = (
        db.query(TaskRun)
        .filter(TaskRun.host_id == host_id, TaskRun.status == RunStatus.QUEUED)
        .order_by(TaskRun.id)
        .limit(limit)
        .all()
    )
    for run in runs:
        _ensure_run_transition(run, RunStatus.DISPATCHED)
        run.status = RunStatus.DISPATCHED
    db.commit()
    payload = []
    for run in runs:
        task = db.get(Task, run.task_id)
        device = db.get(Device, run.device_id)
        payload.append(
            RunAgentOut(
                id=run.id,
                task_id=run.task_id,
                host_id=run.host_id,
                device_id=run.device_id,
                device_serial=device.serial if device else None,
                task_type=task.type if task else "",
                task_params=task.params if task else {},
                tool_id=task.tool_id if task else None,
                tool_snapshot=task.tool_snapshot if task else None,
                pipeline_def=task.pipeline_def if task else None,
            )
        )
    return payload


@router.post("/agent/runs/{run_id}/heartbeat")
def agent_run_heartbeat(run_id: int, payload: RunUpdate, db: Session = Depends(get_db), _: bool = Depends(verify_agent_secret)):
    run = db.get(TaskRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="run not found")

    # 更新进度信息
    if payload.progress is not None:
        run.progress = payload.progress
    if payload.progress_message:
        run.progress_message = payload.progress_message

    normalized_status = _normalize_run_status(payload.status)
    if normalized_status:
        try:
            target_status = RunStatus(normalized_status)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid run status")
        _ensure_run_transition(run, target_status)
        run.status = target_status
    if payload.started_at:
        run.started_at = payload.started_at
    if payload.finished_at:
        run.finished_at = payload.finished_at
    if payload.exit_code is not None:
        run.exit_code = payload.exit_code
    if payload.error_code:
        run.error_code = payload.error_code
    if payload.error_message:
        run.error_message = payload.error_message
    if payload.log_summary:
        run.log_summary = payload.log_summary
    run.last_heartbeat_at = datetime.utcnow()
    if run.status == RunStatus.RUNNING:
        if run.started_at is None:
            run.started_at = datetime.utcnow()
        task = db.get(Task, run.task_id)
        if task:
            _ensure_task_transition(task, TaskStatus.RUNNING)
            task.status = TaskStatus.RUNNING

            # Create RunStep records for pipeline tasks on first RUNNING transition
            if task.pipeline_def and not db.query(RunStep).filter(RunStep.run_id == run.id).first():
                _create_run_steps_from_pipeline(db, run.id, task.pipeline_def)

        _extend_device_lock(db, run.device_id, run.id)

    # 任务完成时，更新分布式任务的整体状态
    if run.status in [RunStatus.FINISHED, RunStatus.FAILED]:
        task = db.get(Task, run.task_id)
        if task and task.is_distributed:
            _update_distributed_task_status(db, task.id)

    db.commit()

    # Broadcast log lines to WebSocket if provided
    if payload.log_lines:
        try:
            # Lazy import to avoid circular dependency
            from backend.api.routes.websocket import manager
            # Get device serial for log context
            device = db.get(Device, run.device_id)
            device_serial = device.serial if device else "unknown"
            timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

            # Broadcast each log line to run_id channel
            for line in payload.log_lines:
                log_data = {
                    "type": "LOG",
                    "payload": {
                        "timestamp": timestamp,
                        "level": "INFO",
                        "device": device_serial,
                        "message": line,
                        "run_id": run_id,
                    },
                }
                asyncio.create_task(manager.broadcast(f"/ws/logs/{run_id}", log_data))

                # 如果是分布式任务，同时广播到 group_id channel
                if run.group_id:
                    asyncio.create_task(manager.broadcast(f"/ws/logs/group/{run.group_id}", log_data))

            # Also broadcast progress if provided
            if payload.progress is not None:
                progress_data = {
                    "type": "PROGRESS",
                    "payload": {
                        "progress": payload.progress,
                        "progress_message": payload.progress_message or "",
                        "run_id": run_id,
                        "device": device_serial,
                    },
                }
                asyncio.create_task(manager.broadcast(f"/ws/logs/{run_id}", progress_data))

                if run.group_id:
                    asyncio.create_task(manager.broadcast(f"/ws/logs/group/{run.group_id}", progress_data))
        except ImportError:
            logger.warning("websocket_manager_not_available")
        except Exception as e:
            logger.warning(f"log_broadcast_failed: {e}")

    return {"ok": True}


@router.post("/agent/runs/{run_id}/complete")
async def agent_run_complete(
    run_id: int,
    payload: Dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
    _: bool = Depends(verify_agent_secret),
):
    parsed_payload: RunCompleteIn
    if "update" in payload:
        # New contract
        try:
            parsed_payload = RunCompleteIn(**payload)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"invalid complete payload: {exc}")
    else:
        # Legacy contract compatibility layer
        logger.warning("legacy_run_complete_payload", extra={"run_id": run_id})
        legacy_status = _normalize_run_status(payload.get("status"))
        if legacy_status is None:
            raise HTTPException(status_code=400, detail="status required")

        legacy_update = RunUpdate(
            status=legacy_status,
            error_code=payload.get("error_code"),
            error_message=payload.get("error_message"),
            exit_code=payload.get("exit_code"),
            started_at=payload.get("started_at"),
            finished_at=payload.get("finished_at"),
            log_summary=payload.get("log_summary"),
        )
        artifact = payload.get("artifact")
        try:
            parsed_payload = RunCompleteIn(
                update=legacy_update,
                artifact=LogArtifactIn(**artifact) if isinstance(artifact, dict) else None,
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"invalid legacy complete payload: {exc}")

    run = db.get(TaskRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="run not found")
    if not parsed_payload.update.status:
        raise HTTPException(status_code=400, detail="status required")
    try:
        target_status = RunStatus(_normalize_run_status(parsed_payload.update.status))
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid run status")
    _ensure_run_transition(run, target_status)
    run.status = target_status
    run.finished_at = parsed_payload.update.finished_at or datetime.utcnow()
    run.exit_code = parsed_payload.update.exit_code
    run.error_code = parsed_payload.update.error_code
    run.error_message = parsed_payload.update.error_message
    run.log_summary = parsed_payload.update.log_summary
    if parsed_payload.artifact:
        db.add(
            LogArtifact(
                run_id=run.id,
                storage_uri=parsed_payload.artifact.storage_uri,
                size_bytes=parsed_payload.artifact.size_bytes,
                checksum=parsed_payload.artifact.checksum,
            )
        )
    task = db.get(Task, run.task_id)
    if task:
        if run.status == RunStatus.FINISHED:
            _ensure_task_transition(task, TaskStatus.COMPLETED)
            task.status = TaskStatus.COMPLETED
        elif run.status == RunStatus.FAILED:
            _ensure_task_transition(task, TaskStatus.FAILED)
            task.status = TaskStatus.FAILED
        elif run.status == RunStatus.CANCELED:
            _ensure_task_transition(task, TaskStatus.CANCELED)
            task.status = TaskStatus.CANCELED
    _release_device_lock(db, run.device_id, run.id)
    db.commit()
    logger.info(
        "run_completed",
        extra={
            "run_id": run.id,
            "task_id": run.task_id,
            "host_id": run.host_id,
            "device_id": run.device_id,
            "run_status": run.status.value,
            "error_code": run.error_code,
            "error_message": run.error_message,
        },
    )
    if run.status == RunStatus.FAILED:
        logger.warning(
            f"run_failed_detail: run_id={run.id}, error_code={run.error_code}, "
            f"error_message={run.error_message}, log_summary={run.log_summary}"
        )

    # Broadcast real-time status updates via WebSocket
    try:
        await broadcast_run_update(run.id, run.task_id, run.status.value, 100, "completed")
        await broadcast_task_update(run.task_id, task.status.value if task else None)
    except Exception as e:
        logger.warning(f"ws_broadcast_on_complete_failed: {e}")

    # Fire-and-forget: auto-generate report + JIRA draft in background
    from backend.services.post_completion import run_post_completion_async
    run_post_completion_async(run.id)

    return {"ok": True}


@router.post("/agent/runs/{run_id}/extend_lock")
def extend_device_lock(
    run_id: int,
    db: Session = Depends(get_db),
    _: bool = Depends(verify_agent_secret),
):
    """
    延长设备锁租期
    由 Agent 定期调用以维持锁，防止长任务执行时锁过期
    """
    now = datetime.utcnow()

    # 查询运行记录
    run = db.get(TaskRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="run not found")

    if run.status != RunStatus.RUNNING:
        raise HTTPException(
            status_code=409,
            detail=f"run is not running, current status: {run.status.value}",
        )

    # 验证设备锁
    device = db.get(Device, run.device_id)
    if not device:
        raise HTTPException(status_code=404, detail="device not found")

    if device.lock_run_id != run_id:
        raise HTTPException(
            status_code=409,
            detail="lock lost, device may be reassigned",
        )

    # 计算新的过期时间
    new_expires = now + timedelta(seconds=DEVICE_LOCK_LEASE_SECONDS)

    # 更新锁过期时间和心跳时间
    device.lock_expires_at = new_expires
    device.last_heartbeat = now
    run.last_heartbeat_at = now

    db.commit()

    logger.info(
        "lock_extended",
        extra={
            "run_id": run_id,
            "device_id": device.id,
            "expires_at": new_expires.isoformat(),
        },
    )

    return {
        "status": "ok",
        "run_id": run_id,
        "device_id": device.id,
        "expires_at": new_expires.isoformat(),
        "extended_at": now.isoformat(),
    }


@router.post("/agent/logs", response_model=AgentLogOut)
def query_agent_logs(query: AgentLogQuery, db: Session = Depends(get_db), _: bool = Depends(verify_agent_secret)):
    """通过SSH查询Linux host上的agent日志"""
    host = db.get(Host, query.host_id)
    if not host:
        raise HTTPException(status_code=404, detail="host not found")

    # Validate log_path to prevent command injection
    # Only allow alphanumeric, dash, underscore, dot, slash, and space
    import re
    if not re.match(r'^[a-zA-Z0-9_./\-\s]+$', query.log_path):
        raise HTTPException(status_code=400, detail="Invalid log_path: contains disallowed characters")

    # 构建SSH连接参数
    ssh_host = host.ip
    ssh_port = host.ssh_port or 22
    ssh_user = host.ssh_user or "root"
    ssh_password = host.extra.get("ssh_password") if host.extra else None
    ssh_key_path = host.extra.get("ssh_key_path") if host.extra else None

    try:
        # 建立SSH连接
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        connect_kwargs = {
            "hostname": ssh_host,
            "port": ssh_port,
            "username": ssh_user,
            "timeout": 10,
        }

        if ssh_key_path and os.path.exists(ssh_key_path):
            connect_kwargs["key_filename"] = ssh_key_path
        elif ssh_password:
            connect_kwargs["password"] = ssh_password
        else:
            # 尝试使用默认密钥
            pass

        client.connect(**connect_kwargs)

        # 执行命令读取日志
        cmd = f"tail -n {query.lines} {shlex.quote(query.log_path)} 2>/dev/null || echo 'LOG_FILE_NOT_FOUND'"
        stdin, stdout, stderr = client.exec_command(cmd)
        content = stdout.read().decode("utf-8", errors="replace")
        error = stderr.read().decode("utf-8", errors="replace")

        client.close()

        if content.strip() == "LOG_FILE_NOT_FOUND":
            return AgentLogOut(
                host_id=query.host_id,
                log_path=query.log_path,
                content="",
                lines_read=0,
                error=f"Log file not found: {query.log_path}",
            )

        lines_read = len([l for l in content.split("\n") if l.strip()])

        return AgentLogOut(
            host_id=query.host_id,
            log_path=query.log_path,
            content=content,
            lines_read=lines_read,
            error=error if error else None,
        )

    except paramiko.AuthenticationException:
        return AgentLogOut(
            host_id=query.host_id,
            log_path=query.log_path,
            content="",
            lines_read=0,
            error="SSH authentication failed. Please check ssh_user and ssh_password/ssh_key.",
        )
    except paramiko.SSHException as e:
        return AgentLogOut(
            host_id=query.host_id,
            log_path=query.log_path,
            content="",
            lines_read=0,
            error=f"SSH connection error: {str(e)}",
        )
    except Exception as e:
        return AgentLogOut(
            host_id=query.host_id,
            log_path=query.log_path,
            content="",
            lines_read=0,
            error=f"Failed to query agent logs: {str(e)}",
        )


@router.post("/tasks/{task_id}/cancel", response_model=RunOut)
def cancel_task(task_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user), request: Request = None):
    """
    Cancel a pending or queued task.
    If the task is already running, it will be marked for cancellation.
    """
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")

    # Can only cancel tasks that are pending, queued, or running
    if task.status not in (TaskStatus.PENDING, TaskStatus.QUEUED, TaskStatus.RUNNING):
        raise HTTPException(
            status_code=409,
            detail=f"cannot cancel task in status {task.status.value}"
        )

    # Get the latest run for this task
    run = (
        db.query(TaskRun)
        .filter(TaskRun.task_id == task_id)
        .order_by(TaskRun.id.desc())
        .first()
    )

    # Update task status
    prev_task_status = task.status.value
    _ensure_task_transition(task, TaskStatus.CANCELED)
    task.status = TaskStatus.CANCELED

    # Update run status if exists and not already finished
    if run and run.status in (RunStatus.QUEUED, RunStatus.DISPATCHED, RunStatus.RUNNING):
        run.status = RunStatus.CANCELED
        run.finished_at = datetime.utcnow()
        # Release device lock if held
        if run.device_id:
            _release_device_lock(db, run.device_id, run.id)
        # Cancel all pending/running RunSteps for this run
        pending_steps = db.query(RunStep).filter(
            RunStep.run_id == run.id,
            RunStep.status.in_([RunStepStatus.PENDING, RunStepStatus.RUNNING]),
        ).all()
        for step in pending_steps:
            step.status = RunStepStatus.CANCELED

    record_audit(
        db,
        action="cancel",
        resource_type="task",
        resource_id=task.id,
        details={
            "run_id": run.id if run else None,
            "from_status": prev_task_status,
            "to_status": TaskStatus.CANCELED.value,
        },
        user_id=current_user.id,
        username=current_user.username,
        request=request,
    )
    db.commit()
    db.refresh(run if run else task)
    if run:
        return run
    # Return a synthetic RunOut for tasks without runs
    return RunOut(
        id=0,
        task_id=task.id,
        host_id=0,
        device_id=task.target_device_id or 0,
        status=RunStatus.CANCELED,
        started_at=None,
        finished_at=datetime.utcnow(),
        exit_code=None,
        error_message="Task canceled by user",
    )


@router.post("/tasks/{task_id}/retry", response_model=TaskOut)
def retry_task(task_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    """
    Retry a failed or canceled task.
    Resets task to PENDING status - dispatcher will create new run when assigning host/device.
    """
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")

    # Can only retry failed or canceled tasks
    if task.status not in (TaskStatus.FAILED, TaskStatus.CANCELED):
        raise HTTPException(
            status_code=409,
            detail=f"cannot retry task in status {task.status.value}"
        )

    # Check if target device is available (if specified)
    if task.target_device_id:
        device = db.get(Device, task.target_device_id)
        if not device or device.status != DeviceStatus.ONLINE:
            raise HTTPException(
                status_code=409,
                detail="target device is not available"
            )

    # Reset task status to pending - dispatcher will handle creating the run
    task.status = TaskStatus.PENDING
    db.commit()
    db.refresh(task)
    return task


# ---------------------------------------------------------------------------
# Batch Operations
# ---------------------------------------------------------------------------

class BatchTaskIds(BaseModel):
    task_ids: List[int]


class BatchResult(BaseModel):
    success: List[int] = Field(default_factory=list)
    failed: List[dict] = Field(default_factory=list)
    total: int = 0


@router.post("/tasks/batch/cancel", response_model=BatchResult)
def batch_cancel_tasks(
    payload: BatchTaskIds,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Cancel multiple tasks at once."""
    result = BatchResult(total=len(payload.task_ids))

    for task_id in payload.task_ids:
        task = db.get(Task, task_id)
        if not task:
            result.failed.append({"task_id": task_id, "error": "not found"})
            continue

        if task.status not in (TaskStatus.PENDING, TaskStatus.QUEUED, TaskStatus.RUNNING):
            result.failed.append({"task_id": task_id, "error": f"cannot cancel in status {task.status.value}"})
            continue

        try:
            task.status = TaskStatus.CANCELED
            # Cancel active runs
            active_runs = (
                db.query(TaskRun)
                .filter(
                    TaskRun.task_id == task_id,
                    TaskRun.status.in_([RunStatus.QUEUED, RunStatus.DISPATCHED, RunStatus.RUNNING]),
                )
                .all()
            )
            for run in active_runs:
                run.status = RunStatus.CANCELED
                run.finished_at = datetime.utcnow()
                if run.device_id:
                    _release_device_lock(db, run.device_id, run.id)
            result.success.append(task_id)
            # 每个任务单独提交，确保成功即持久化
            db.commit()
        except Exception as exc:
            # 失败时回滚当前任务的操作
            db.rollback()
            result.failed.append({"task_id": task_id, "error": str(exc)})

    return result


@router.post("/tasks/batch/retry", response_model=BatchResult)
def batch_retry_tasks(
    payload: BatchTaskIds,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Retry multiple failed/canceled tasks at once."""
    result = BatchResult(total=len(payload.task_ids))

    for task_id in payload.task_ids:
        task = db.get(Task, task_id)
        if not task:
            result.failed.append({"task_id": task_id, "error": "not found"})
            continue

        if task.status not in (TaskStatus.FAILED, TaskStatus.CANCELED):
            result.failed.append({"task_id": task_id, "error": f"cannot retry in status {task.status.value}"})
            continue

        try:
            task.status = TaskStatus.PENDING
            result.success.append(task_id)
            # 每个任务单独提交，确保成功即持久化
            db.commit()
        except Exception as exc:
            # 失败时回滚当前任务的操作
            db.rollback()
            result.failed.append({"task_id": task_id, "error": str(exc)})

    return result


@router.delete("/tasks/{task_id}")
def delete_task(
    task_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    Delete a task (only allowed for terminal or pending states).
    Rejects if the task has active (QUEUED/DISPATCHED/RUNNING) runs.
    """
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")

    allowed_statuses = {TaskStatus.PENDING, TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELED}
    if task.status not in allowed_statuses:
        raise HTTPException(
            status_code=409,
            detail=f"cannot delete task in status {task.status.value}; must be PENDING, COMPLETED, FAILED, or CANCELED",
        )

    # Check for active runs
    active_run_count = (
        db.query(TaskRun)
        .filter(
            TaskRun.task_id == task_id,
            TaskRun.status.in_([RunStatus.QUEUED, RunStatus.DISPATCHED, RunStatus.RUNNING]),
        )
        .count()
    )
    if active_run_count > 0:
        raise HTTPException(
            status_code=409,
            detail=f"task has {active_run_count} active run(s); cancel them first",
        )

    # Delete associated runs and artifacts
    runs = db.query(TaskRun).filter(TaskRun.task_id == task_id).all()
    for run in runs:
        db.query(LogArtifact).filter(LogArtifact.run_id == run.id).delete()
    db.query(TaskRun).filter(TaskRun.task_id == task_id).delete()

    db.delete(task)
    db.commit()

    return {"message": "删除成功"}


# ==================== RunStep API (Pipeline 子步骤) ====================


@router.get("/runs/{run_id}/steps", response_model=List[RunStepOut])
def list_run_steps(run_id: int, db: Session = Depends(get_db)):
    """List all pipeline steps for a given TaskRun, ordered by phase and step_order."""
    run = db.get(TaskRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="run not found")
    steps = (
        db.query(RunStep)
        .filter(RunStep.run_id == run_id)
        .order_by(RunStep.phase, RunStep.step_order)
        .all()
    )
    return steps


@router.get("/runs/{run_id}/steps/{step_id}", response_model=RunStepOut)
def get_run_step(run_id: int, step_id: int, db: Session = Depends(get_db)):
    """Get a single RunStep detail."""
    step = db.query(RunStep).filter(RunStep.id == step_id, RunStep.run_id == run_id).first()
    if not step:
        raise HTTPException(status_code=404, detail="step not found")
    return step


@router.post("/agent/runs/{run_id}/steps/{step_id}/status")
def agent_update_step_status(
    run_id: int,
    step_id: int,
    payload: RunStepUpdate,
    db: Session = Depends(get_db),
    _: bool = Depends(verify_agent_secret),
):
    """Agent HTTP fallback for step status updates."""
    step = db.query(RunStep).filter(RunStep.id == step_id, RunStep.run_id == run_id).first()
    if not step:
        raise HTTPException(status_code=404, detail="step not found")

    # Valid RunStep status transitions
    _STEP_TRANSITIONS = {
        RunStepStatus.PENDING: {RunStepStatus.RUNNING, RunStepStatus.SKIPPED, RunStepStatus.CANCELED},
        RunStepStatus.RUNNING: {RunStepStatus.COMPLETED, RunStepStatus.FAILED, RunStepStatus.CANCELED},
        RunStepStatus.COMPLETED: set(),
        RunStepStatus.FAILED: set(),
        RunStepStatus.SKIPPED: set(),
        RunStepStatus.CANCELED: set(),
    }

    if payload.status:
        try:
            target_status = RunStepStatus(payload.status)
            allowed = _STEP_TRANSITIONS.get(step.status, set())
            if target_status not in allowed and step.status != target_status:
                raise HTTPException(
                    status_code=409,
                    detail=f"illegal step transition {step.status.value}->{payload.status}",
                )
            step.status = target_status
        except ValueError:
            raise HTTPException(status_code=400, detail=f"invalid step status: {payload.status}")
    if payload.started_at:
        step.started_at = payload.started_at
    if payload.finished_at:
        step.finished_at = payload.finished_at
    if payload.exit_code is not None:
        step.exit_code = payload.exit_code
    if payload.error_message is not None:
        step.error_message = payload.error_message
    if payload.log_line_count is not None:
        step.log_line_count = payload.log_line_count

    db.commit()
    db.refresh(step)

    # Broadcast step update to frontend WebSocket subscribers
    try:
        from backend.api.routes.websocket import schedule_broadcast
        # Use {type, payload} envelope to match the WS agent relay protocol
        schedule_broadcast(f"/ws/logs/{run_id}", {
            "type": "STEP_UPDATE",
            "payload": {
                "step_id": step.id,
                "name": step.name,
                "phase": step.phase,
                "status": step.status.value if hasattr(step.status, 'value') else str(step.status),
                "started_at": step.started_at.isoformat() if step.started_at else None,
                "finished_at": step.finished_at.isoformat() if step.finished_at else None,
                "exit_code": step.exit_code,
                "error_message": step.error_message,
            },
        })
    except Exception:
        logger.warning("Failed to broadcast step update via WebSocket", exc_info=True)

    return {"status": "ok"}
