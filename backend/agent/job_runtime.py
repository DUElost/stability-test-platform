"""Job worker pool + recovery sync wiring extracted from ``main`` (#736).

Starts outbox drain, recovery execute/resume bindings, step-trace uploader,
the admitted-job ThreadPoolExecutor, and periodic recovery sync.
"""

from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable, Dict, Set

from .active_job_bindings import JobRunnerStateSlot
from .control_handler import ControlHandlerDeps
from .heartbeat_bindings import RecoveryActionsSlot
from .job_runner import JobRunnerState
from .outbox_drainer import OutboxDrainThread
from .patrol_recovery import build_patrol_job_not_running_handler
from .recovery_executor import run_recovery_sync_if_needed
from .recovery_runtime import (
    ResumeJobSlot,
    build_cancel_recovery_job,
    build_execute_recovery_actions,
    build_resume_recovered_job,
    start_periodic_recovery_sync,
)
from .step_trace_uploader import StepTraceUploader


@dataclass
class JobRuntime:
    """Running job / recovery plane after the host control plane is up."""

    outbox_drain: OutboxDrainThread
    executor: ThreadPoolExecutor
    job_runner_state: JobRunnerState
    step_trace_uploader: StepTraceUploader
    recovery_sync_stop: threading.Event
    recovery_sync_thread: threading.Thread
    execute_recovery_actions: Callable[..., Any]


def start_job_runtime(
    *,
    api_url: str,
    host_id: str,
    agent_instance_id: str,
    boot_id: str,
    agent_secret: str,
    local_db: Any,
    lease_renewer: Any,
    register_active_job: Callable[..., None],
    deregister_active_job: Callable[..., None],
    job_runner_slot: JobRunnerStateSlot,
    recovery_actions_slot: RecoveryActionsSlot,
    control_deps: ControlHandlerDeps,
    coordinator: Any,
    operation_scheduler: Any,
    adb: Any,
    mq_producer: Any,
    script_registry: Any,
    patrol_checkpoint_store: Any,
    run_task_wrapper: Callable[..., Any],
    active_jobs_lock: threading.Lock,
    active_job_ids: Set[int],
    active_device_ids: Set[int],
    active_job_tokens: Dict[int, str],
    active_device_owner: Dict[int, int],
    device_id_register: Callable[[int], None],
    device_id_deregister: Callable[[int], None],
    watcher_globally_enabled: bool,
    watcher_plan_default: bool,
) -> JobRuntime:
    """Wire outbox, recovery, step-trace, executor, and JobRunnerState."""
    outbox_drain = OutboxDrainThread(api_url, local_db, interval=15.0)
    outbox_drain.start()

    # ── ADR-0019 Phase 3a: Recovery Sync ──
    resume_slot = ResumeJobSlot()
    cancel_recovery_job = build_cancel_recovery_job(job_runner_slot)
    execute_recovery_actions = build_execute_recovery_actions(
        lease_renewer=lease_renewer,
        local_db=local_db,
        outbox_drain=outbox_drain,
        register_active_job=register_active_job,
        resume_slot=resume_slot,
        cancel_recovery_job=cancel_recovery_job,
    )
    recovery_actions_slot.value = execute_recovery_actions

    patrol_job_not_running_recovery = build_patrol_job_not_running_handler(
        api_url=api_url,
        host_id=host_id,
        agent_instance_id=agent_instance_id,
        boot_id=boot_id,
        local_db=local_db,
        execute_actions=execute_recovery_actions,
    )

    # StepTrace HTTP 批量上报（Phase 3.7: acked=0 补传 → Phase 4: 唯一上报路径）
    step_trace_uploader = StepTraceUploader(
        api_url, local_db, agent_secret=agent_secret, interval=5.0,
    )
    step_trace_uploader.start()

    # ADR-0026 Step 5b: thread pool sized for ALL devices the host manages
    # (up to ~50), NOT permit-limited. Concurrent script/ADB operations are
    # gated by the OperationScheduler; distinct device jobs share the pool
    # and wait for their turn.
    max_workers = int(os.getenv("STP_JOB_WORKER_POOL_SIZE", "50"))
    executor = ThreadPoolExecutor(
        max_workers=max_workers, thread_name_prefix="task-worker"
    )
    job_runner_state = JobRunnerState(
        active_jobs_lock=active_jobs_lock,
        active_job_ids=active_job_ids,
        active_device_ids=active_device_ids,
        active_job_tokens=active_job_tokens,
        running_worker_tokens={},
        watcher_globally_enabled=watcher_globally_enabled,
        watcher_plan_default=watcher_plan_default,
        lock_register=register_active_job,
        lock_deregister=deregister_active_job,
        device_id_register=device_id_register,
        device_id_deregister=device_id_deregister,
        active_device_owner=active_device_owner,
        on_job_not_running_recovery=patrol_job_not_running_recovery,
    )
    control_deps.job_runner_state = job_runner_state
    job_runner_slot.value = job_runner_state

    resume_slot.value = build_resume_recovered_job(
        agent_instance_id=agent_instance_id,
        coordinator=coordinator,
        executor=executor,
        adb=adb,
        api_url=api_url,
        host_id=host_id,
        job_runner_state=job_runner_state,
        mq_producer=mq_producer,
        script_registry=script_registry,
        local_db=local_db,
        patrol_checkpoint_store=patrol_checkpoint_store,
        operation_scheduler=operation_scheduler,
        step_trace_uploader=step_trace_uploader,
        run_task_wrapper=run_task_wrapper,
    )

    # Recovery sync execution（启动一次 + #784 周期兜底）
    run_recovery_sync_if_needed(
        local_db=local_db,
        api_url=api_url,
        host_id=host_id,
        agent_instance_id=agent_instance_id,
        boot_id=boot_id,
        execute_actions=execute_recovery_actions,
    )
    recovery_sync_stop, recovery_sync_thread, _interval = start_periodic_recovery_sync(
        local_db=local_db,
        api_url=api_url,
        host_id=host_id,
        agent_instance_id=agent_instance_id,
        boot_id=boot_id,
        execute_actions=execute_recovery_actions,
    )

    return JobRuntime(
        outbox_drain=outbox_drain,
        executor=executor,
        job_runner_state=job_runner_state,
        step_trace_uploader=step_trace_uploader,
        recovery_sync_stop=recovery_sync_stop,
        recovery_sync_thread=recovery_sync_thread,
        execute_recovery_actions=execute_recovery_actions,
    )
