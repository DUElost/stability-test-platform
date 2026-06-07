"""Thread-pool job execution wrapper for the agent."""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, MutableMapping, Optional

from .api_client import complete_job, update_job
from .config import get_run_log_dir
from .job_session import JobSession, JobStartupError
from .pipeline_runner import execute_pipeline_run
from .watcher.enable import job_wants_watcher

logger = logging.getLogger(__name__)


@dataclass
class JobRunnerState:
    active_jobs_lock: Any
    active_job_ids: set[int]
    active_device_ids: set[int]
    active_job_tokens: MutableMapping[int, str]
    running_worker_tokens: MutableMapping[int, str]
    watcher_globally_enabled: bool
    watcher_plan_default: bool
    lock_register: Callable[..., None]
    lock_deregister: Callable[..., None]
    device_id_register: Callable[[int], None]
    device_id_deregister: Callable[[int], None]
    on_job_not_running_recovery: Optional[Callable[[int], None]] = None

    def is_aborted(self, job_id: int, worker_token: str) -> bool:
        with self.active_jobs_lock:
            return (
                job_id not in self.active_job_ids
                or self.active_job_tokens.get(job_id, "") != worker_token
            )

    def try_mark_worker_started(self, job_id: int, worker_token: str) -> bool:
        with self.active_jobs_lock:
            if self.running_worker_tokens.get(job_id) == worker_token:
                return False
            self.running_worker_tokens[job_id] = worker_token
            return True

    def release(
        self,
        job_id: int,
        fencing_token: str,
        device_id: Optional[int],
        local_worker_token: str = "",
    ) -> None:
        worker_token = local_worker_token or fencing_token
        self.lock_deregister(job_id, fencing_token, worker_token)
        with self.active_jobs_lock:
            if self.running_worker_tokens.get(job_id) == worker_token:
                self.running_worker_tokens.pop(job_id, None)


def _validate_pipeline_def(pipeline_def: Optional[Dict[str, Any]]) -> Optional[str]:
    """Validate pipeline structure via shared semantic validator (imported from core).

    This function is a thin adapter that converts the shared
    ``validate_lifecycle_semantics`` return type to the str-or-None form
    expected by the agent job runner.
    """
    try:
        from backend.core.pipeline_validator import validate_lifecycle_semantics
    except ImportError:
        from .pipeline_validator import validate_lifecycle_semantics

    if not pipeline_def or not isinstance(pipeline_def, dict):
        return "pipeline_def is required"

    ok_sem, errors = validate_lifecycle_semantics(pipeline_def)
    if ok_sem:
        return None
    return "; ".join(errors)


def _complete_job_if_current_worker(
    *,
    state: JobRunnerState,
    job_id: int,
    local_worker_token: str,
    api_url: str,
    payload: Dict[str, Any],
    fencing_token: str,
    local_db: Optional[Any],
    suppress_reason: str,
) -> bool:
    if state.is_aborted(job_id, local_worker_token):
        logger.info(
            "run_complete_suppressed_superseded_worker job_id=%d status=%s reason=%s worker=%s",
            job_id,
            payload.get("status"),
            suppress_reason,
            local_worker_token,
        )
        return False

    complete_job(
        api_url,
        job_id,
        payload,
        fencing_token=fencing_token,
        local_db=local_db,
    )
    return True


def run_task_wrapper(
    run: Dict[str, Any],
    adb: Any,
    api_url: str,
    host_id: str,
    state: JobRunnerState,
    mq_producer: Optional[Any] = None,
    script_registry: Optional[Any] = None,
    local_db: Optional[Any] = None,
) -> None:
    """Run a claimed job in a worker thread and report its terminal state."""
    job_id = run["id"]
    fencing_token = run["fencing_token"]  # ADR-0019 Phase 2b: 强协议，缺字段直接暴露协议错误
    local_worker_token = run.get("local_worker_token") or fencing_token
    if not state.try_mark_worker_started(job_id, local_worker_token):
        logger.info(
            "run_skip_duplicate_worker job_id=%d token=%s worker=%s",
            job_id,
            fencing_token[:8] if fencing_token else "",
            local_worker_token,
        )
        return

    task_id = run.get("task_id")
    device_id = run.get("device_id")
    device_serial = run.get("device_serial", "")
    pipeline_def = run.get("pipeline_def")

    # ADR-0019 Phase 3a: register fencing_token + persist active_job before any work begins
    state.lock_register(job_id, fencing_token, device_id, device_serial, local_worker_token)

    logger.info(
        "run_start job_id=%d task_id=%s device_id=%s device_serial=%s",
        job_id, task_id, device_id, device_serial,
    )

    try:
        update_job(
            api_url,
            job_id,
            {"status": "RUNNING", "started_at": datetime.now(timezone.utc).isoformat(), "fencing_token": fencing_token},
        )
    except Exception as exc:
        logger.warning(
            "Heartbeat confirmation for job %s failed (non-fatal): %s",
            job_id,
            exc,
        )

    pipeline_error = _validate_pipeline_def(pipeline_def)
    if pipeline_error is not None:
        _complete_job_if_current_worker(
            state=state,
            job_id=job_id,
            local_worker_token=local_worker_token,
            api_url=api_url,
            payload={
                "status": "FAILED",
                "exit_code": 1,
                "error_code": "PIPELINE_REQUIRED",
                "error_message": pipeline_error,
            },
            fencing_token=fencing_token,
            local_db=local_db,
            suppress_reason="pipeline_invalid",
        )
        state.release(job_id, fencing_token, device_id, local_worker_token=local_worker_token)
        return

    session: Optional[JobSession] = None
    if job_wants_watcher(
        run,
        globally_enabled=state.watcher_globally_enabled,
        plan_default=state.watcher_plan_default,
    ):
        try:
            session = JobSession(
                job_payload=run,
                host_id=host_id,
                log_dir=str(get_run_log_dir(job_id)),
                lock_register=lambda _jid: None,
                lock_deregister=lambda _jid: None,
                device_id_register=None,
                device_id_deregister=None,
            )
            session.__enter__()
        except JobStartupError as exc:
            logger.error(
                "job_session_start_failed job_id=%d reason=%s: %s",
                job_id, exc.reason_code, exc,
            )
            _complete_job_if_current_worker(
                state=state,
                job_id=job_id,
                local_worker_token=local_worker_token,
                api_url=api_url,
                payload={
                    "status": "FAILED",
                    "exit_code": 1,
                    "error_code": "WATCHER_START_FAIL",
                    "error_message": str(exc),
                },
                fencing_token=fencing_token,
                local_db=local_db,
                suppress_reason="watcher_start_failed",
            )
            state.release(job_id, fencing_token, device_id, local_worker_token=local_worker_token)
            return

    try:
        result = execute_pipeline_run(
            pipeline_def,
            job_id,
            device_serial,
            adb,
            api_url,
            host_id=host_id,
            mq_producer=mq_producer,
            script_registry=script_registry,
            local_db=local_db,
            is_aborted=lambda: state.is_aborted(job_id, local_worker_token),
            fencing_token=fencing_token,
            on_job_not_running_recovery=state.on_job_not_running_recovery,
            watcher_capability=(
                session.summary.watcher_capability if session is not None else None
            ),
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

        if _complete_job_if_current_worker(
            state=state,
            job_id=job_id,
            local_worker_token=local_worker_token,
            api_url=api_url,
            payload=complete_payload,
            fencing_token=fencing_token,
            local_db=local_db,
            suppress_reason="worker_superseded_after_run",
        ):
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
        _complete_job_if_current_worker(
            state=state,
            job_id=job_id,
            local_worker_token=local_worker_token,
            api_url=api_url,
            payload=failure_payload,
            fencing_token=fencing_token,
            local_db=local_db,
            suppress_reason="worker_superseded_after_exception",
        )
    finally:
        state.release(job_id, fencing_token, device_id, local_worker_token=local_worker_token)
