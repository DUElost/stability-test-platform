# -*- coding: utf-8 -*-
"""
Workflow Executor

Background daemon that advances RUNNING workflows by:
1. Monitoring running steps (polling TaskRun status)
2. Launching next pending step when previous completes
3. Marking workflow COMPLETED or FAILED as appropriate

Similar pattern to TaskDispatcher — runs in its own daemon thread.
"""

import logging
import os
import threading
import time
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session, selectinload

from backend.core.database import SessionLocal
from backend.models.schemas import (
    Device,
    DeviceStatus,
    Host,
    HostStatus,
    RunStatus,
    StepStatus,
    Task,
    TaskRun,
    TaskStatus,
    Workflow,
    WorkflowStatus,
    WorkflowStep,
)
from backend.api.routes.websocket import schedule_broadcast

logger = logging.getLogger(__name__)

WORKFLOW_POLL_INTERVAL = float(os.getenv("WORKFLOW_POLL_INTERVAL", "5"))


class WorkflowExecutor:
    """Drives RUNNING workflows forward step-by-step."""

    def start(self) -> threading.Thread:
        thread = threading.Thread(
            target=self._run_loop,
            name="workflow-executor",
            daemon=True,
        )
        thread.start()
        logger.info("workflow_executor_started")
        return thread

    def _run_loop(self) -> None:
        while True:
            try:
                self._tick()
            except Exception:
                logger.exception("workflow_executor_tick_failed")
            time.sleep(WORKFLOW_POLL_INTERVAL)

    def _tick(self) -> None:
        with SessionLocal() as db:
            running_workflows = (
                db.query(Workflow)
                .options(selectinload(Workflow.steps))
                .filter(Workflow.status == WorkflowStatus.RUNNING)
                .all()
            )
            for wf in running_workflows:
                try:
                    self._advance_workflow(db, wf)
                except Exception:
                    logger.exception(
                        "workflow_advance_failed",
                        extra={"workflow_id": wf.id},
                    )

    def _advance_workflow(self, db: Session, wf: Workflow) -> None:
        steps = sorted(wf.steps, key=lambda s: s.order)

        # 1. Check for any currently running step — sync its status
        running_step = next(
            (s for s in steps if s.status == StepStatus.RUNNING), None
        )
        if running_step:
            self._sync_step_status(db, running_step)
            if running_step.status == StepStatus.RUNNING:
                # Still running — nothing more to do this tick
                return

        # 2. Re-evaluate after sync: check for failures
        for step in steps:
            if step.status == StepStatus.FAILED:
                wf.status = WorkflowStatus.FAILED
                wf.finished_at = datetime.utcnow()
                db.commit()
                self._broadcast_workflow_update(wf)
                logger.info(
                    "workflow_failed",
                    extra={"workflow_id": wf.id, "failed_step": step.id},
                )
                return

        # 3. Find next pending step
        next_step = next(
            (s for s in steps if s.status == StepStatus.PENDING), None
        )
        if next_step is None:
            # All steps done (no pending, no running, no failed)
            wf.status = WorkflowStatus.COMPLETED
            wf.finished_at = datetime.utcnow()
            db.commit()
            self._broadcast_workflow_update(wf)
            logger.info("workflow_completed", extra={"workflow_id": wf.id})
            return

        # 4. Launch the next step
        self._launch_step(db, wf, next_step)

    def _sync_step_status(self, db: Session, step: WorkflowStep) -> None:
        """Sync step status from its linked TaskRun."""
        if not step.task_run_id:
            # Step is RUNNING but has no linked run — check if it timed out
            if step.started_at:
                elapsed = (datetime.utcnow() - step.started_at).total_seconds()
                if elapsed > 15:
                    step.status = StepStatus.FAILED
                    step.error_message = "Dispatcher did not create a task run within timeout"
                    step.finished_at = datetime.utcnow()
                    db.commit()
            return
        run = db.get(TaskRun, step.task_run_id)
        if not run:
            return

        if run.status == RunStatus.FINISHED:
            step.status = StepStatus.COMPLETED
            step.finished_at = datetime.utcnow()
            db.commit()
        elif run.status in (RunStatus.FAILED, RunStatus.CANCELED):
            step.status = StepStatus.FAILED
            step.error_message = run.error_message or f"run {run.status.value}"
            step.finished_at = datetime.utcnow()
            db.commit()

    def _launch_step(self, db: Session, wf: Workflow, step: WorkflowStep) -> None:
        """Create a Task and TaskRun for the step, then mark it RUNNING."""

        # Determine task type
        task_type = step.task_type or "UNKNOWN"
        if step.tool_id:
            from backend.models.schemas import Tool
            tool = db.get(Tool, step.tool_id)
            if tool:
                task_type = tool.name

        # Create a Task record
        task = Task(
            name=f"[WF-{wf.id}] Step {step.order}: {step.name}",
            type=task_type,
            params=step.params or {},
            target_device_id=step.target_device_id,
            status=TaskStatus.PENDING,
        )
        if step.tool_id:
            task.tool_id = step.tool_id
        db.add(task)
        db.flush()

        # Wait for dispatcher to create a TaskRun, then link it.
        # Keep step PENDING until we have a task_run_id to avoid the race
        # condition where _sync_step_status skips steps with no run link.
        task_id = task.id
        step.started_at = datetime.utcnow()
        db.commit()

        for _ in range(10):
            time.sleep(1)
            db.expire(step)
            run = (
                db.query(TaskRun)
                .filter(TaskRun.task_id == task_id)
                .first()
            )
            if run:
                step.task_run_id = run.id
                step.status = StepStatus.RUNNING
                db.commit()
                break
        else:
            # Timeout — dispatcher never created a run
            step.status = StepStatus.FAILED
            step.error_message = "Dispatcher did not create a task run within timeout"
            step.finished_at = datetime.utcnow()
            db.commit()

        self._broadcast_workflow_update(wf)
        logger.info(
            "workflow_step_launched",
            extra={
                "workflow_id": wf.id,
                "step_id": step.id,
                "step_order": step.order,
                "task_id": task_id,
            },
        )

    def _broadcast_workflow_update(self, wf: Workflow) -> None:
        schedule_broadcast("/ws/dashboard", {
            "type": "WORKFLOW_UPDATE",
            "payload": {
                "workflow_id": wf.id,
                "status": wf.status.value,
            },
        })


_executor: Optional[WorkflowExecutor] = None


def start_workflow_executor() -> threading.Thread:
    global _executor
    _executor = WorkflowExecutor()
    return _executor.start()
