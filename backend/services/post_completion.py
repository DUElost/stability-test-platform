# -*- coding: utf-8 -*-
"""
Post-completion Pipeline

Automatically generates a RunReport and JIRA draft when a run finishes,
caching the results on the TaskRun record.  Designed to run in a background
thread (fire-and-forget) so it doesn't block the API response.
"""

import logging
from datetime import datetime
from typing import Optional

from backend.core.database import SessionLocal
from backend.models.schemas import TaskRun
from backend.services.report_service import compose_run_report, build_jira_draft
from backend.api.routes.websocket import schedule_broadcast

logger = logging.getLogger(__name__)


def run_post_completion(run_id: int) -> None:
    """
    Generate report + JIRA draft for *run_id* and cache on the TaskRun row.
    Opens its own DB session so it is safe to call from any thread.
    """
    try:
        db = SessionLocal()
        try:
            report = compose_run_report(db, run_id)
            if report is None:
                logger.warning("post_completion_skip_no_report", extra={"run_id": run_id})
                return

            jira_draft = build_jira_draft(report)

            run = db.get(TaskRun, run_id)
            if run is None:
                logger.warning("post_completion_skip_no_run", extra={"run_id": run_id})
                return

            # Serialize to JSON-safe dicts
            if hasattr(report, "model_dump"):
                run.report_json = report.model_dump(mode="json")
            else:
                run.report_json = report.dict()

            if hasattr(jira_draft, "model_dump"):
                run.jira_draft_json = jira_draft.model_dump(mode="json")
            else:
                run.jira_draft_json = jira_draft.dict()

            run.post_processed_at = datetime.utcnow()
            db.commit()

            logger.info(
                "post_completion_done",
                extra={"run_id": run_id, "task_id": run.task_id},
            )

            # Broadcast REPORT_READY via WebSocket
            schedule_broadcast("/ws/dashboard", {
                "type": "REPORT_READY",
                "payload": {
                    "run_id": run_id,
                    "task_id": run.task_id,
                },
            })

            # Dispatch notification based on run status
            from backend.services.notification_service import dispatch_notification_async
            from backend.models.schemas import RunStatus, Task
            from backend.models.host import Device
            event = "RUN_COMPLETED" if run.status == RunStatus.FINISHED else "RUN_FAILED"
            task_obj = db.get(Task, run.task_id)
            device_obj = db.get(Device, run.device_id) if run.device_id else None
            dispatch_notification_async(event, {
                "run_id": run_id,
                "task_id": run.task_id,
                "task_name": task_obj.name if task_obj else "unknown",
                "task_type": task_obj.type if task_obj else "unknown",
                "device_serial": device_obj.serial if device_obj else "N/A",
                "error_message": run.error_message,
                "status": run.status.value if hasattr(run.status, "value") else str(run.status),
            })
        finally:
            db.close()
    except Exception:
        logger.exception("post_completion_failed", extra={"run_id": run_id})


def run_post_completion_async(run_id: int) -> None:
    """Fire-and-forget wrapper — submits to bounded thread pool.

    In testing mode or when pool is shut down, runs synchronously.
    """
    import os
    from concurrent.futures import ThreadPoolExecutor

    # In testing mode, run synchronously to avoid pool shutdown issues
    if os.getenv("TESTING") == "1":
        run_post_completion(run_id)
        return

    try:
        from backend.core.thread_pool import submit as pool_submit
        pool_submit(run_post_completion, run_id)
    except RuntimeError as e:
        # Pool is shut down (e.g., during test teardown); run synchronously
        if "cannot schedule new futures after shutdown" in str(e):
            run_post_completion(run_id)
        else:
            raise
