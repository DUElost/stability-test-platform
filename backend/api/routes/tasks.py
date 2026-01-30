import logging
import os
from datetime import datetime, timedelta
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from ...core.database import get_db
from ...models.schemas import Device, DeviceStatus, Host, LogArtifact, RunStatus, Task, TaskRun, TaskStatus
from ..schemas import (
    LogArtifactIn,
    RunAgentOut,
    RunCompleteIn,
    RunOut,
    RunUpdate,
    TaskCreate,
    TaskDispatch,
    TaskOut,
)

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


@router.get("/tasks", response_model=List[TaskOut])
def list_tasks(db: Session = Depends(get_db)):
    """获取任务列表"""
    return db.query(Task).order_by(Task.id.desc()).all()


@router.post("/tasks", response_model=TaskOut)
def create_task(payload: TaskCreate, db: Session = Depends(get_db)):
    task = Task(
        name=payload.name,
        type=payload.type,
        template_id=payload.template_id,
        params=payload.params,
        target_device_id=payload.target_device_id,
        status=TaskStatus.PENDING,
        priority=payload.priority,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


@router.get("/tasks/{task_id}", response_model=TaskOut)
def get_task(task_id: int, db: Session = Depends(get_db)):
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    return task


@router.post("/tasks/{task_id}/dispatch", response_model=RunOut)
def dispatch_task(task_id: int, payload: TaskDispatch, db: Session = Depends(get_db)):
    host = db.get(Host, payload.host_id)
    device = db.get(Device, payload.device_id)
    if not host or not device:
        raise HTTPException(status_code=400, detail="host or device not found")

    with db.begin():
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
    db.refresh(run)
    return run


@router.get("/agent/runs/pending", response_model=List[RunAgentOut])
def agent_pending_runs(
    host_id: int = Query(...),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
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
            )
        )
    return payload


@router.post("/agent/runs/{run_id}/heartbeat")
def agent_run_heartbeat(run_id: int, payload: RunUpdate, db: Session = Depends(get_db)):
    run = db.get(TaskRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="run not found")
    if payload.status:
        try:
            target_status = RunStatus(payload.status)
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
        _extend_device_lock(db, run.device_id, run.id)
    db.commit()
    return {"ok": True}


@router.post("/agent/runs/{run_id}/complete")
def agent_run_complete(
    run_id: int,
    payload: RunCompleteIn,
    db: Session = Depends(get_db),
):
    run = db.get(TaskRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="run not found")
    if not payload.update.status:
        raise HTTPException(status_code=400, detail="status required")
    try:
        target_status = RunStatus(payload.update.status)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid run status")
    _ensure_run_transition(run, target_status)
    run.status = target_status
    run.finished_at = payload.update.finished_at or datetime.utcnow()
    run.exit_code = payload.update.exit_code
    run.error_code = payload.update.error_code
    run.error_message = payload.update.error_message
    run.log_summary = payload.update.log_summary
    if payload.artifact:
        db.add(
            LogArtifact(
                run_id=run.id,
                storage_uri=payload.artifact.storage_uri,
                size_bytes=payload.artifact.size_bytes,
                checksum=payload.artifact.checksum,
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
    return {"ok": True}
