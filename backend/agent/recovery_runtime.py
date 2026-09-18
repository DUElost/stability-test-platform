"""Recovery sync wiring extracted from ``main`` (#736).

Owns cancel / execute / resume closures and the periodic recovery-sync thread.
Late-bound pieces use slots: ``JobRunnerStateSlot`` (shared with occupancy
bindings) and ``ResumeJobSlot`` (filled after the worker pool exists).
"""

from __future__ import annotations

import logging
import os
import threading
from concurrent.futures import Executor
from dataclasses import dataclass
from typing import Any, Callable, Optional

from .active_job_bindings import JobRunnerStateSlot
from .recovery_executor import (
    _coerce_recovery_interval,
    execute_recovery_actions_impl,
    run_recovery_sync_if_needed,
)

logger = logging.getLogger(__name__)


@dataclass
class ResumeJobSlot:
    """Mutable cell for the resume callable (set after JobRunnerState exists)."""

    value: Any = None


def build_cancel_recovery_job(
    job_runner_slot: JobRunnerStateSlot,
) -> Callable[[int], None]:
    def cancel_recovery_job(jid: int) -> None:
        if job_runner_slot.value is not None:
            job_runner_slot.value.request_abort(jid)

    return cancel_recovery_job


def build_execute_recovery_actions(
    *,
    lease_renewer: Any,
    local_db: Any,
    outbox_drain: Any,
    register_active_job: Callable[..., None],
    resume_slot: ResumeJobSlot,
    cancel_recovery_job: Callable[[int], None],
) -> Callable[[dict, dict], None]:
    """ADR-0019 Phase 3a: Backend recovery actions → local occupancy / resume."""

    def execute_recovery_actions(resp: dict, active_jobs_by_id: dict) -> None:
        execute_recovery_actions_impl(
            resp=resp,
            active_jobs_by_id=active_jobs_by_id,
            lease_renewer=lease_renewer,
            local_db=local_db,
            outbox_drain=outbox_drain,
            register_active_job=register_active_job,
            resume_job=resume_slot.value,
            abort_local_job=cancel_recovery_job,
        )

    return execute_recovery_actions


def build_resume_recovered_job(
    *,
    agent_instance_id: str,
    coordinator: Any,
    executor: Executor,
    adb: Any,
    api_url: str,
    host_id: str,
    job_runner_state: Any,
    mq_producer: Any,
    script_registry: Any,
    local_db: Any,
    patrol_checkpoint_store: Any,
    operation_scheduler: Any,
    step_trace_uploader: Any,
    run_task_wrapper: Callable[..., Any],
) -> Callable[[dict], None]:
    def resume_recovered_job(job_payload: dict) -> None:
        job_payload.setdefault("agent_instance_id", agent_instance_id)
        # ADR-0026 Step 5b: recovered jobs go through scheduler/coordinator.
        jid = job_payload.get("id")
        did = job_payload.get("device_id")
        if jid and did:
            coordinator.register_job(jid)
            coordinator.register_job_device(jid, did)
        prh_id = job_payload.get("plan_run_host_id")
        plan_run_id = job_payload.get("plan_run_id")
        if prh_id and plan_run_id:
            coordinator.register_plan_run_host(prh_id, plan_run_id)
        executor.submit(
            run_task_wrapper,
            job_payload,
            adb,
            api_url,
            host_id,
            job_runner_state,
            mq_producer,
            script_registry,
            local_db,
            patrol_checkpoint_store,
            operation_scheduler=operation_scheduler,
            coordinator=coordinator,
            step_trace_uploader=step_trace_uploader,  # #483
        )

    return resume_recovered_job


def start_periodic_recovery_sync(
    *,
    local_db: Any,
    api_url: str,
    host_id: str,
    agent_instance_id: str,
    boot_id: str,
    execute_actions: Callable[[dict, dict], None],
    interval_env: Optional[str] = None,
) -> tuple[threading.Event, threading.Thread, float]:
    """#784: periodic recovery reconcile; returns (stop_event, thread, interval)."""
    # Recovery sync execution (one-shot at start is caller's responsibility)
    stop_event = threading.Event()
    interval = _coerce_recovery_interval(
        interval_env
        if interval_env is not None
        else os.getenv("STP_RECOVERY_SYNC_INTERVAL_SECONDS", "60")
    )

    def recovery_sync_loop() -> None:
        while not stop_event.wait(interval):
            try:
                run_recovery_sync_if_needed(
                    local_db=local_db,
                    api_url=api_url,
                    host_id=host_id,
                    agent_instance_id=agent_instance_id,
                    boot_id=boot_id,
                    execute_actions=execute_actions,
                )
            except Exception:
                logger.exception("recovery_sync_periodic_failed")

    thread = threading.Thread(
        target=recovery_sync_loop, name="recovery-sync", daemon=True
    )
    thread.start()
    logger.info("recovery_sync_periodic_started interval=%.1fs", interval)
    return stop_event, thread, interval
