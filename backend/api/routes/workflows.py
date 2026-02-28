# -*- coding: utf-8 -*-
"""
Workflow API endpoints

CRUD + start/cancel for multi-step test workflows.
"""

import logging
from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session, selectinload

from backend.core.database import get_db
from backend.core.audit import record_audit
from backend.models.schemas import (
    Workflow,
    WorkflowStatus,
    WorkflowStep,
    StepStatus,
)
from backend.api.schemas import (
    PaginatedResponse,
    WorkflowCreate,
    WorkflowOut,
    WorkflowStepOut,
)
from backend.api.routes.auth import get_current_active_user, User

router = APIRouter(prefix="/api/v1", tags=["workflows"])
logger = logging.getLogger(__name__)


@router.get("/workflows", response_model=PaginatedResponse)
def list_workflows(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    base = (
        db.query(Workflow)
        .options(selectinload(Workflow.steps))
        .order_by(Workflow.created_at.desc())
    )
    total = base.count()
    rows = base.offset(skip).limit(limit).all()
    items = [_workflow_to_out(w) for w in rows]
    return PaginatedResponse(items=items, total=total, skip=skip, limit=limit)


@router.post("/workflows", response_model=WorkflowOut)
def create_workflow(
    payload: WorkflowCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    request: Request = None,
):
    wf = Workflow(
        name=payload.name,
        description=payload.description,
        status=WorkflowStatus.DRAFT,
        created_by=current_user.id,
    )
    db.add(wf)
    db.flush()  # get wf.id

    for idx, step_data in enumerate(payload.steps, start=1):
        step = WorkflowStep(
            workflow_id=wf.id,
            order=idx,
            name=step_data.name,
            tool_id=step_data.tool_id,
            task_type=step_data.task_type,
            params=step_data.params,
            target_device_id=step_data.target_device_id,
            status=StepStatus.PENDING,
        )
        db.add(step)

    record_audit(
        db,
        action="create",
        resource_type="workflow",
        resource_id=wf.id,
        details={"name": wf.name, "steps_count": len(payload.steps)},
        user_id=current_user.id,
        username=current_user.username,
        request=request,
    )
    db.commit()
    db.refresh(wf)
    logger.info("workflow_created", extra={"workflow_id": wf.id, "steps": len(payload.steps)})
    return _workflow_to_out(wf)


@router.get("/workflows/{workflow_id}", response_model=WorkflowOut)
def get_workflow(workflow_id: int, db: Session = Depends(get_db)):
    wf = (
        db.query(Workflow)
        .options(selectinload(Workflow.steps))
        .filter(Workflow.id == workflow_id)
        .first()
    )
    if not wf:
        raise HTTPException(status_code=404, detail="workflow not found")
    return _workflow_to_out(wf)


@router.post("/workflows/{workflow_id}/start", response_model=WorkflowOut)
def start_workflow(
    workflow_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    request: Request = None,
):
    wf = (
        db.query(Workflow)
        .options(selectinload(Workflow.steps))
        .filter(Workflow.id == workflow_id)
        .first()
    )
    if not wf:
        raise HTTPException(status_code=404, detail="workflow not found")
    if wf.status not in (WorkflowStatus.DRAFT, WorkflowStatus.READY):
        raise HTTPException(
            status_code=409,
            detail=f"cannot start workflow in status {wf.status.value}",
        )
    prev_status = wf.status.value
    wf.status = WorkflowStatus.RUNNING
    wf.started_at = datetime.utcnow()
    record_audit(
        db,
        action="start",
        resource_type="workflow",
        resource_id=wf.id,
        details={"from_status": prev_status},
        user_id=current_user.id,
        username=current_user.username,
        request=request,
    )
    db.commit()
    db.refresh(wf)
    logger.info("workflow_started", extra={"workflow_id": wf.id})
    return _workflow_to_out(wf)


@router.post("/workflows/{workflow_id}/cancel", response_model=WorkflowOut)
def cancel_workflow(
    workflow_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    request: Request = None,
):
    wf = (
        db.query(Workflow)
        .options(selectinload(Workflow.steps))
        .filter(Workflow.id == workflow_id)
        .first()
    )
    if not wf:
        raise HTTPException(status_code=404, detail="workflow not found")
    if wf.status in (WorkflowStatus.COMPLETED, WorkflowStatus.CANCELED):
        raise HTTPException(
            status_code=409,
            detail=f"cannot cancel workflow in status {wf.status.value}",
        )
    prev_status = wf.status.value
    wf.status = WorkflowStatus.CANCELED
    wf.finished_at = datetime.utcnow()
    # Mark pending/running steps as SKIPPED
    skipped_count = 0
    for step in wf.steps:
        if step.status in (StepStatus.PENDING, StepStatus.RUNNING):
            step.status = StepStatus.SKIPPED
            step.finished_at = datetime.utcnow()
            skipped_count += 1
    record_audit(
        db,
        action="cancel",
        resource_type="workflow",
        resource_id=wf.id,
        details={"from_status": prev_status, "skipped_steps": skipped_count},
        user_id=current_user.id,
        username=current_user.username,
        request=request,
    )
    db.commit()
    db.refresh(wf)
    logger.info("workflow_canceled", extra={"workflow_id": wf.id})
    return _workflow_to_out(wf)


@router.delete("/workflows/{workflow_id}")
def delete_workflow(
    workflow_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    request: Request = None,
):
    wf = db.get(Workflow, workflow_id)
    if not wf:
        raise HTTPException(status_code=404, detail="workflow not found")
    if wf.status not in (WorkflowStatus.DRAFT,):
        raise HTTPException(
            status_code=409,
            detail="can only delete DRAFT workflows",
        )
    wf_name = wf.name
    wf_status = wf.status.value
    wf_steps_count = len(wf.steps)
    db.delete(wf)
    record_audit(
        db,
        action="delete",
        resource_type="workflow",
        resource_id=workflow_id,
        details={"name": wf_name, "status": wf_status, "steps_count": wf_steps_count},
        user_id=current_user.id,
        username=current_user.username,
        request=request,
    )
    db.commit()
    logger.info("workflow_deleted", extra={"workflow_id": workflow_id})
    return {"ok": True}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _workflow_to_out(wf: Workflow) -> WorkflowOut:
    steps_out = []
    for step in wf.steps:
        if hasattr(WorkflowStepOut, "model_validate"):
            steps_out.append(WorkflowStepOut.model_validate(step))
        else:
            steps_out.append(WorkflowStepOut.from_orm(step))

    if hasattr(WorkflowOut, "model_validate"):
        out = WorkflowOut.model_validate(wf)
    else:
        out = WorkflowOut.from_orm(wf)
    out.steps = steps_out
    return out


# ==================== Clone & Template ====================

@router.post("/workflows/{workflow_id}/clone", response_model=WorkflowOut)
def clone_workflow(
    workflow_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """克隆工作流"""
    wf = db.query(Workflow).filter_by(id=workflow_id).first()
    if not wf:
        raise HTTPException(status_code=404, detail="Workflow not found")

    new_wf = Workflow(
        name=f"{wf.name} (副本)",
        description=wf.description,
        status=WorkflowStatus.DRAFT,
        is_template=False,
    )
    db.add(new_wf)
    db.flush()

    for step in wf.steps:
        new_step = WorkflowStep(
            workflow_id=new_wf.id,
            order=step.order,
            name=step.name,
            tool_id=step.tool_id,
            task_type=step.task_type,
            target_device_id=step.target_device_id,
            params=step.params,
            status=StepStatus.PENDING,
        )
        db.add(new_step)

    db.commit()
    db.refresh(new_wf)
    return _build_workflow_out(db, new_wf)


@router.post("/workflows/{workflow_id}/toggle-template", response_model=WorkflowOut)
def toggle_template(
    workflow_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """切换工作流的模板状态"""
    wf = db.query(Workflow).filter_by(id=workflow_id).first()
    if not wf:
        raise HTTPException(status_code=404, detail="Workflow not found")

    wf.is_template = not wf.is_template
    db.commit()
    db.refresh(wf)
    return _build_workflow_out(db, wf)


def _build_workflow_out(db: Session, wf: Workflow) -> WorkflowOut:
    steps_out = []
    for step in wf.steps:
        if hasattr(WorkflowStepOut, "model_validate"):
            steps_out.append(WorkflowStepOut.model_validate(step))
        else:
            steps_out.append(WorkflowStepOut.from_orm(step))

    if hasattr(WorkflowOut, "model_validate"):
        out = WorkflowOut.model_validate(wf)
    else:
        out = WorkflowOut.from_orm(wf)
    out.steps = steps_out
    return out
