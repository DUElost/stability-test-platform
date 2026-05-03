# -*- coding: utf-8 -*-
"""
Task Schedules API — CRUD + toggle + run-now for cron-based scheduling.
"""

import asyncio
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from backend.core.database import AsyncSessionLocal, get_db
from backend.core.audit import record_audit
from backend.models.host import Device
from backend.models.schedule import TaskSchedule
from backend.models.workflow import WorkflowDefinition
from backend.api.routes.auth import get_current_active_user, User
from backend.api.schemas import (
    PaginatedResponse,
    TaskScheduleCreate,
    TaskScheduleUpdate,
    TaskScheduleOut,
)
from backend.services.dispatcher import DispatchError, dispatch_workflow

router = APIRouter(prefix="/api/v1/schedules", tags=["schedules"])


def _compute_next_run(cron_expression: str, after: datetime) -> datetime:
    from croniter import croniter
    cron = croniter(cron_expression, after)
    return cron.get_next(datetime)


def _validate_cron(expr: str) -> None:
    from croniter import croniter
    if not croniter.is_valid(expr):
        raise HTTPException(status_code=400, detail=f"无效的 cron 表达式: {expr}")


def _field_provided(model, field_name: str) -> bool:
    if hasattr(model, "model_fields_set"):
        return field_name in model.model_fields_set
    return field_name in getattr(model, "__fields_set__", set())


def _validate_workflow_schedule(
    db: Session,
    workflow_definition_id: Optional[int],
    device_ids: List[int],
) -> None:
    if workflow_definition_id is None:
        return

    wf = db.get(WorkflowDefinition, workflow_definition_id)
    if not wf:
        raise HTTPException(status_code=400, detail=f"工作流不存在: {workflow_definition_id}")
    if not device_ids:
        raise HTTPException(status_code=400, detail="工作流定时任务至少需要一个 device_id")

    rows = db.query(Device.id).filter(Device.id.in_(device_ids)).all()
    existing = {int(r[0]) for r in rows}
    missing = sorted(set(int(x) for x in device_ids) - existing)
    if missing:
        raise HTTPException(status_code=400, detail=f"设备不存在: {missing}")


def _dispatch_workflow_sync(workflow_definition_id: int, device_ids: List[int]) -> int:
    async def _run() -> int:
        async with AsyncSessionLocal() as adb:
            wf = await adb.get(WorkflowDefinition, workflow_definition_id)
            if wf is None:
                raise DispatchError(f"workflow not found: {workflow_definition_id}")
            run = await dispatch_workflow(
                workflow_def_id=workflow_definition_id,
                device_ids=device_ids,
                failure_threshold=wf.failure_threshold,
                triggered_by="schedule",
                db=adb,
            )
            return int(run.id)

    return asyncio.run(_run())


# ==================== CRUD ====================

@router.get("", response_model=PaginatedResponse)
def list_schedules(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """获取定时任务列表"""
    query = db.query(TaskSchedule).order_by(TaskSchedule.id.desc())
    total = query.count()
    items = query.offset(skip).limit(limit).all()
    result = [
        TaskScheduleOut.model_validate(s) if hasattr(TaskScheduleOut, "model_validate")
        else TaskScheduleOut.from_orm(s)
        for s in items
    ]
    return PaginatedResponse(items=result, total=total, skip=skip, limit=limit)


@router.get("/{schedule_id}", response_model=TaskScheduleOut)
def get_schedule(schedule_id: int, db: Session = Depends(get_db)):
    """获取定时任务详情"""
    sched = db.query(TaskSchedule).filter_by(id=schedule_id).first()
    if not sched:
        raise HTTPException(status_code=404, detail="定时任务不存在")
    return sched


@router.post("", response_model=TaskScheduleOut)
def create_schedule(
    data: TaskScheduleCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    request: Request = None,
):
    """创建定时任务"""
    _validate_cron(data.cron_expression)
    _validate_workflow_schedule(db, data.workflow_definition_id, data.device_ids or [])

    now = datetime.now(timezone.utc)
    is_workflow_mode = data.workflow_definition_id is not None
    task_type = "WORKFLOW" if is_workflow_mode else (data.task_type or "WORKFLOW")
    sched = TaskSchedule(
        name=data.name,
        cron_expression=data.cron_expression,
        task_template_id=None if is_workflow_mode else data.task_template_id,
        tool_id=None if is_workflow_mode else data.tool_id,
        task_type=task_type,
        params=data.params,
        target_device_id=None if is_workflow_mode else data.target_device_id,
        workflow_definition_id=data.workflow_definition_id,
        device_ids=(data.device_ids or None) if is_workflow_mode else None,
        enabled=data.enabled,
        created_by=current_user.id,
        next_run_at=_compute_next_run(data.cron_expression, now) if data.enabled else None,
    )
    db.add(sched)
    db.flush()
    record_audit(
        db,
        action="create",
        resource_type="schedule",
        resource_id=sched.id,
        details={"name": sched.name, "cron_expression": sched.cron_expression,
                 "enabled": sched.enabled, "task_type": sched.task_type,
                 "tool_id": sched.tool_id, "target_device_id": sched.target_device_id,
                 "workflow_definition_id": sched.workflow_definition_id, "device_ids": sched.device_ids},
        user_id=current_user.id,
        username=current_user.username,
        request=request,
    )
    db.commit()
    db.refresh(sched)
    return sched


@router.put("/{schedule_id}", response_model=TaskScheduleOut)
def update_schedule(
    schedule_id: int,
    data: TaskScheduleUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    request: Request = None,
):
    """更新定时任务"""
    sched = db.query(TaskSchedule).filter_by(id=schedule_id).first()
    if not sched:
        raise HTTPException(status_code=404, detail="定时任务不存在")

    if data.cron_expression is not None:
        _validate_cron(data.cron_expression)
        sched.cron_expression = data.cron_expression

    if data.name is not None:
        sched.name = data.name

    # 支持显式将 workflow_definition_id 置空（从新链路切回 legacy）
    if _field_provided(data, "workflow_definition_id"):
        sched.workflow_definition_id = data.workflow_definition_id
    if data.device_ids is not None:
        sched.device_ids = data.device_ids

    if sched.workflow_definition_id is not None:
        _validate_workflow_schedule(db, sched.workflow_definition_id, sched.device_ids or [])
        sched.task_type = "WORKFLOW"
        sched.task_template_id = None
        sched.tool_id = None
        sched.target_device_id = None
    else:
        if data.task_template_id is not None:
            sched.task_template_id = data.task_template_id
        if data.tool_id is not None:
            sched.tool_id = data.tool_id
        if data.task_type is not None:
            sched.task_type = data.task_type
        if data.target_device_id is not None:
            sched.target_device_id = data.target_device_id

    if data.params is not None:
        sched.params = data.params
    if data.enabled is not None:
        sched.enabled = data.enabled

    # Recompute next_run if enabled
    if sched.enabled:
        sched.next_run_at = _compute_next_run(sched.cron_expression, datetime.now(timezone.utc))
    else:
        sched.next_run_at = None

    record_audit(
        db,
        action="update",
        resource_type="schedule",
        resource_id=sched.id,
        details={"name": sched.name, "cron_expression": sched.cron_expression,
                 "enabled": sched.enabled, "task_type": sched.task_type,
                 "workflow_definition_id": sched.workflow_definition_id, "device_ids": sched.device_ids},
        user_id=current_user.id,
        username=current_user.username,
        request=request,
    )
    db.commit()
    db.refresh(sched)
    return sched


@router.delete("/{schedule_id}")
def delete_schedule(
    schedule_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    request: Request = None,
):
    """删除定时任务"""
    sched = db.query(TaskSchedule).filter_by(id=schedule_id).first()
    if not sched:
        raise HTTPException(status_code=404, detail="定时任务不存在")
    sched_name = sched.name
    sched_cron = sched.cron_expression
    sched_enabled = sched.enabled
    db.delete(sched)
    record_audit(
        db,
        action="delete",
        resource_type="schedule",
        resource_id=schedule_id,
        details={"name": sched_name, "cron_expression": sched_cron, "enabled": sched_enabled},
        user_id=current_user.id,
        username=current_user.username,
        request=request,
    )
    db.commit()
    return {"message": "删除成功"}


# ==================== Actions ====================

@router.post("/{schedule_id}/toggle", response_model=TaskScheduleOut)
def toggle_schedule(
    schedule_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """切换定时任务启用/禁用"""
    sched = db.query(TaskSchedule).filter_by(id=schedule_id).first()
    if not sched:
        raise HTTPException(status_code=404, detail="定时任务不存在")

    sched.enabled = not sched.enabled
    if sched.enabled:
        sched.next_run_at = _compute_next_run(sched.cron_expression, datetime.now(timezone.utc))
    else:
        sched.next_run_at = None

    db.commit()
    db.refresh(sched)
    return sched


@router.post("/{schedule_id}/run-now")
def run_schedule_now(
    schedule_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """立即执行定时任务（优先触发 WorkflowRun；legacy 模式回退创建 Task）。"""
    sched = db.query(TaskSchedule).filter_by(id=schedule_id).first()
    if not sched:
        raise HTTPException(status_code=404, detail="定时任务不存在")

    if sched.workflow_definition_id is None:
        raise HTTPException(
            status_code=400,
            detail="该定时任务未关联 WorkflowDefinition，无法执行。"
                   "请编辑该定时任务并关联一个工作流定义。",
        )

    device_ids = [int(x) for x in (sched.device_ids or [])]
    _validate_workflow_schedule(db, sched.workflow_definition_id, device_ids)
    try:
        workflow_run_id = _dispatch_workflow_sync(sched.workflow_definition_id, device_ids)
    except DispatchError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {
        "message": "工作流已触发",
        "workflow_run_id": workflow_run_id,
        "task_id": None,
    }
