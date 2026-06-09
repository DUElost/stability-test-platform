# -*- coding: utf-8 -*-
"""
Task Schedules API — CRUD + toggle + run-now for Plan-based cron scheduling.
"""

from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from backend.core.database import get_db
from backend.core.audit import record_audit
from backend.core.legacy_aee import LEGACY_AEE_SCRIPT_NAMES
from backend.models.host import Device
from backend.models.schedule import TaskSchedule, schedule_timestamp
from backend.models.plan import Plan, PlanStep
from backend.api.routes.auth import get_current_active_user, User
from backend.api.schemas import (
    PaginatedResponse,
    TaskScheduleCreate,
    TaskScheduleUpdate,
    TaskScheduleOut,
)
from backend.services.plan_dispatcher_sync import PlanDispatchError, dispatch_plan_sync

router = APIRouter(prefix="/api/v1/schedules", tags=["schedules"])


def _compute_next_run(cron_expression: str, after: datetime) -> datetime:
    from croniter import croniter
    cron = croniter(cron_expression, after)
    return schedule_timestamp(cron.get_next(datetime))


def _validate_cron(expr: str) -> None:
    from croniter import croniter
    if not croniter.is_valid(expr):
        raise HTTPException(status_code=400, detail=f"无效的 cron 表达式: {expr}")


def _field_provided(model, field_name: str) -> bool:
    if hasattr(model, "model_fields_set"):
        return field_name in model.model_fields_set
    return field_name in getattr(model, "__fields_set__", set())


def _plan_has_hidden_legacy_aee_steps(db: Session, plan_id: int) -> bool:
    rows = (
        db.query(PlanStep.script_name)
        .filter(PlanStep.plan_id == plan_id)
        .all()
    )
    return any(str(row[0]) in LEGACY_AEE_SCRIPT_NAMES for row in rows)


def _raise_if_hidden_legacy_aee_plan_id(
    db: Session,
    plan_id: int,
    *,
    status_code: int,
) -> None:
    if _plan_has_hidden_legacy_aee_steps(db, plan_id):
        if status_code == 400:
            raise HTTPException(status_code=400, detail=f"Plan 不存在: {plan_id}")
        raise HTTPException(status_code=status_code, detail="定时任务不存在")


def _raise_if_hidden_legacy_aee_schedule(
    db: Session,
    sched: TaskSchedule | None,
) -> TaskSchedule:
    if not sched:
        raise HTTPException(status_code=404, detail="定时任务不存在")
    _raise_if_hidden_legacy_aee_plan_id(db, int(sched.plan_id), status_code=404)
    return sched


def _validate_plan_schedule(
    db: Session,
    plan_id: int,
    device_ids: List[int],
) -> None:
    plan = db.get(Plan, plan_id)
    if not plan:
        raise HTTPException(status_code=400, detail=f"Plan 不存在: {plan_id}")
    _raise_if_hidden_legacy_aee_plan_id(db, plan_id, status_code=400)
    if not device_ids:
        raise HTTPException(status_code=400, detail="Plan 定时任务至少需要一个 device_id")

    rows = db.query(Device.id).filter(Device.id.in_(device_ids)).all()
    existing = {int(r[0]) for r in rows}
    missing = sorted(set(int(x) for x in device_ids) - existing)
    if missing:
        raise HTTPException(status_code=400, detail=f"设备不存在: {missing}")


def _dispatch_plan_sync_wrapper(plan_id: int, device_ids: List[int]) -> int:
    from backend.core.database import SessionLocal
    with SessionLocal() as sdb:
        run = dispatch_plan_sync(
            plan_id=plan_id,
            device_ids=device_ids,
            triggered_by="schedule",
            db=sdb,
            run_type="SCHEDULE",
        )
        return int(run.id)


# ==================== CRUD ====================

@router.get("", response_model=PaginatedResponse)
def list_schedules(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    """获取定时任务列表"""
    query = db.query(TaskSchedule).order_by(TaskSchedule.id.desc())
    total = query.count()
    items = query.offset(skip).limit(limit).all()
    visible_items = [
        s for s in items
        if not _plan_has_hidden_legacy_aee_steps(db, int(s.plan_id))
    ]
    result = [TaskScheduleOut.model_validate(s) for s in visible_items]
    return PaginatedResponse(items=result, total=len(result), skip=skip, limit=limit)


@router.get("/{schedule_id}", response_model=TaskScheduleOut)
def get_schedule(
    schedule_id: int,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    """获取定时任务详情"""
    sched = db.query(TaskSchedule).filter_by(id=schedule_id).first()
    sched = _raise_if_hidden_legacy_aee_schedule(db, sched)
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
    _validate_plan_schedule(db, data.plan_id, data.device_ids or [])

    now = datetime.now(timezone.utc)
    sched = TaskSchedule(
        name=data.name,
        cron_expression=data.cron_expression,
        plan_id=data.plan_id,
        device_ids=data.device_ids or None,
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
                 "enabled": sched.enabled, "plan_id": sched.plan_id,
                 "device_ids": sched.device_ids},
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
    sched = _raise_if_hidden_legacy_aee_schedule(db, sched)

    if data.cron_expression is not None:
        _validate_cron(data.cron_expression)
        sched.cron_expression = data.cron_expression

    if data.name is not None:
        sched.name = data.name

    if _field_provided(data, "plan_id") and data.plan_id is not None:
        sched.plan_id = data.plan_id
    if data.device_ids is not None:
        sched.device_ids = data.device_ids

    _validate_plan_schedule(db, sched.plan_id, sched.device_ids or [])

    if data.enabled is not None:
        sched.enabled = data.enabled

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
                 "enabled": sched.enabled, "plan_id": sched.plan_id,
                 "device_ids": sched.device_ids},
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
    sched = _raise_if_hidden_legacy_aee_schedule(db, sched)
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
    sched = _raise_if_hidden_legacy_aee_schedule(db, sched)

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
    """立即执行定时任务（触发 PlanRun）。"""
    sched = db.query(TaskSchedule).filter_by(id=schedule_id).first()
    sched = _raise_if_hidden_legacy_aee_schedule(db, sched)

    device_ids = [int(x) for x in (sched.device_ids or [])]
    _validate_plan_schedule(db, sched.plan_id, device_ids)
    try:
        plan_run_id = _dispatch_plan_sync_wrapper(sched.plan_id, device_ids)
    except PlanDispatchError as exc:
        raise HTTPException(status_code=400, detail=exc.detail())
    return {
        "message": "Plan 已触发",
        "plan_run_id": plan_run_id,
    }
