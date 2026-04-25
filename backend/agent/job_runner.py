"""Thread-pool job execution wrapper for the agent."""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, MutableSet, Optional

from .api_client import complete_job, update_job
from .config import get_run_log_dir
from .job_session import JobSession, JobStartupError
from .pipeline_runner import execute_pipeline_run

logger = logging.getLogger(__name__)


@dataclass
class JobRunnerState:
    active_jobs_lock: Any
    active_job_ids: MutableSet[int]
    active_device_ids: MutableSet[int]
    watcher_enabled: bool
    lock_register: Callable[[int], None]
    lock_deregister: Callable[[int], None]
    device_id_register: Callable[[int], None]
    device_id_deregister: Callable[[int], None]

    def is_aborted(self, job_id: int) -> bool:
        with self.active_jobs_lock:
            return job_id not in self.active_job_ids

    def release(self, job_id: int, device_id: Optional[int]) -> None:
        with self.active_jobs_lock:
            self.active_job_ids.discard(job_id)
            if device_id:
                self.active_device_ids.discard(device_id)


def _validate_pipeline_def(pipeline_def: Optional[Dict[str, Any]]) -> Optional[str]:
    if not (pipeline_def and isinstance(pipeline_def, dict)):
        return "pipeline_def is required"

    is_lifecycle = isinstance(pipeline_def.get("lifecycle"), dict)
    is_stages = isinstance(pipeline_def.get("stages"), dict)
    if not is_lifecycle and not is_stages:
        return "pipeline_def must contain 'stages' or 'lifecycle'"
    if is_stages and not is_lifecycle:
        stages = pipeline_def.get("stages", {})
        has_step = any(
            isinstance(stages.get(key), list) and len(stages.get(key) or []) > 0
            for key in ("prepare", "execute", "post_process")
        )
        if not has_step:
            return "pipeline_def.stages must contain at least one step"
    return None


def run_task_wrapper(
    run: Dict[str, Any],
    adb: Any,
    api_url: str,
    host_id: str,
    ws_client: Any,
    state: JobRunnerState,
    mq_producer: Optional[Any] = None,
    tool_registry: Optional[Any] = None,
    script_registry: Optional[Any] = None,
    local_db: Optional[Any] = None,
) -> None:
    """Run a claimed job in a worker thread and report its terminal state."""
    job_id = run["id"]
    task_id = run.get("task_id")
    device_id = run.get("device_id")
    device_serial = run.get("device_serial", "")
    pipeline_def = run.get("pipeline_def")

    logger.info(
        "run_start job_id=%d task_id=%s device_id=%s device_serial=%s",
        job_id, task_id, device_id, device_serial,
    )

    try:
        update_job(
            api_url,
            job_id,
            {"status": "RUNNING", "started_at": datetime.utcnow().isoformat()},
        )
    except Exception as exc:
        logger.warning(
            "Heartbeat confirmation for job %s failed (non-fatal): %s",
            job_id,
            exc,
        )

    pipeline_error = _validate_pipeline_def(pipeline_def)
    if pipeline_error is not None:
        complete_job(
            api_url,
            job_id,
            {
                "status": "FAILED",
                "exit_code": 1,
                "error_code": "PIPELINE_REQUIRED",
                "error_message": pipeline_error,
            },
            local_db=local_db,
        )
        state.release(job_id, device_id)
        return

    session: Optional[JobSession] = None
    if state.watcher_enabled:
        try:
            session = JobSession(
                job_payload=run,
                host_id=host_id,
                log_dir=str(get_run_log_dir(job_id)),
                lock_register=state.lock_register,
                lock_deregister=state.lock_deregister,
                device_id_register=state.device_id_register,
                device_id_deregister=state.device_id_deregister,
            )
            session.__enter__()
        except JobStartupError as exc:
            logger.error(
                "job_session_start_failed job_id=%d reason=%s: %s",
                job_id, exc.reason_code, exc,
            )
            complete_job(
                api_url,
                job_id,
                {
                    "status": "FAILED",
                    "exit_code": 1,
                    "error_code": "WATCHER_START_FAIL",
                    "error_message": str(exc),
                },
                local_db=local_db,
            )
            state.release(job_id, device_id)
            return

    try:
        result = execute_pipeline_run(
            pipeline_def,
            job_id,
            device_serial,
            adb,
            api_url,
            host_id=host_id,
            ws_client=ws_client,
            mq_producer=mq_producer,
            tool_registry=tool_registry,
            script_registry=script_registry,
            local_db=local_db,
            is_aborted=lambda: state.is_aborted(job_id),
        )

        watcher_summary = None
        if session is not None:
            try:
                session.__exit__(None, None, None)
                watcher_summary = session.summary.to_complete_payload()
            except Exception:
                logger.exception("job_session_exit_failed job_id=%d", job_id)
            finally:
                session = None

        complete_payload = {
            "status": result["status"],
            "exit_code": result["exit_code"],
            "error_code": result.get("error_code"),
            "error_message": result.get("error_message"),
            "log_summary": result.get("log_summary"),
            "artifact": result.get("artifact"),
        }
        if watcher_summary is not None:
            complete_payload["watcher_summary"] = watcher_summary

        complete_job(api_url, job_id, complete_payload, local_db=local_db)
        logger.info("run_complete", extra={"job_id": job_id, "status": result["status"]})
    except Exception as exc:
        logger.exception("run_failed job=%d: %s", job_id, exc)
        watcher_summary = None
        if session is not None:
            try:
                session.__exit__(type(exc), exc, exc.__traceback__)
                watcher_summary = session.summary.to_complete_payload()
            except Exception:
                logger.exception("job_session_exit_failed_on_error job_id=%d", job_id)
            finally:
                session = None
        failure_payload = {
            "status": "FAILED",
            "exit_code": 1,
            "error_code": "AGENT_ERROR",
            "error_message": str(exc),
        }
        if watcher_summary is not None:
            failure_payload["watcher_summary"] = watcher_summary
        complete_job(api_url, job_id, failure_payload, local_db=local_db)
    finally:
        state.release(job_id, device_id)
