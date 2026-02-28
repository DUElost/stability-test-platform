# -*- coding: utf-8 -*-
"""
Task Schedules API — CRUD + toggle + run-now for cron-based scheduling.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from backend.core.database import get_db
from backend.core.audit import record_audit
from backend.models.schemas import Task, TaskSchedule, TaskStatus
from backend.api.routes.auth import get_current_active_user, User
from backend.api.schemas import (
    PaginatedResponse,
    TaskScheduleCreate,
    TaskScheduleUpdate,
    TaskScheduleOut,
)

router = APIRouter(prefix="/api/v1/schedules", tags=["schedules"])


def _compute_next_run(cron_expression: str, after: datetime) -> datetime:
    from croniter import croniter
    cron = croniter(cron_expression, after)
    return cron.get_next(datetime)


def _validate_cron(expr: str) -> None:
    from croniter import croniter
    if not croniter.is_valid(expr):
        raise HTTPException(status_code=400, detail=f"无效的 cron 表达式: {expr}")


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

    now = datetime.utcnow()
    sched = TaskSchedule(
        name=data.name,
        cron_expression=data.cron_expression,
        task_template_id=data.task_template_id,
        tool_id=data.tool_id,
        task_type=data.task_type,
        params=data.params,
        target_device_id=data.target_device_id,
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
                 "tool_id": sched.tool_id, "target_device_id": sched.target_device_id},
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
    if data.task_template_id is not None:
        sched.task_template_id = data.task_template_id
    if data.tool_id is not None:
        sched.tool_id = data.tool_id
    if data.task_type is not None:
        sched.task_type = data.task_type
    if data.params is not None:
        sched.params = data.params
    if data.target_device_id is not None:
        sched.target_device_id = data.target_device_id
    if data.enabled is not None:
        sched.enabled = data.enabled

    # Recompute next_run if enabled
    if sched.enabled:
        sched.next_run_at = _compute_next_run(sched.cron_expression, datetime.utcnow())
    else:
        sched.next_run_at = None

    record_audit(
        db,
        action="update",
        resource_type="schedule",
        resource_id=sched.id,
        details={"name": sched.name, "cron_expression": sched.cron_expression,
                 "enabled": sched.enabled, "task_type": sched.task_type},
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
        sched.next_run_at = _compute_next_run(sched.cron_expression, datetime.utcnow())
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
    """立即执行定时任务（创建一个 PENDING 任务）"""
    sched = db.query(TaskSchedule).filter_by(id=schedule_id).first()
    if not sched:
        raise HTTPException(status_code=404, detail="定时任务不存在")

    now = datetime.utcnow()
    task = Task(
        name=f"[manual] {sched.name} - {now.strftime('%Y%m%d_%H%M')}",
        type=sched.task_type,
        tool_id=sched.tool_id,
        template_id=sched.task_template_id,
        params=sched.params or {},
        target_device_id=sched.target_device_id,
        status=TaskStatus.PENDING,
        priority=0,
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    return {"message": "任务已创建", "task_id": task.id}
