"""Claim loop shell (signal / while / finally) extracted from ``main`` (#736).

``process_claim_tick`` and ``shutdown_agent_runtime`` already live elsewhere;
this module owns the SIGTERM/SIGINT wake-up loop that ties them together.
"""

from __future__ import annotations

import logging
import signal
import threading
from typing import Any, Callable

from .claim_loop import process_claim_tick
from .graceful_shutdown import shutdown_agent_runtime
from .host_control_plane import HostControlPlane
from .job_runtime import JobRuntime

logger = logging.getLogger(__name__)


def run_agent_loop(
    *,
    api_url: str,
    poll_interval: float,
    host_id: str,
    agent_instance_id: str,
    plane: HostControlPlane,
    runtime: JobRuntime,
    heartbeat_thread: Any,
    adb: Any,
    mq_producer: Any,
    script_registry: Any,
    patrol_checkpoint_store: Any,
    run_task_wrapper: Callable[..., Any],
    log_signal_drainer: Any,
    local_db: Any,
    sio_client: Any,
) -> None:
    """Block until SIGTERM/SIGINT, then drain via ``shutdown_agent_runtime``."""
    shutdown_event = threading.Event()

    def _signal_handler(signum, frame):
        sig_name = signal.Signals(signum).name
        logger.info("received_%s, initiating graceful shutdown", sig_name)
        shutdown_event.set()

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    try:
        while not shutdown_event.is_set():
            try:
                process_claim_tick(
                    api_url=api_url,
                    host_id=host_id,
                    agent_instance_id=agent_instance_id,
                    occupancy=plane.occupancy,
                    heartbeat_thread=heartbeat_thread,
                    register_active_job=plane.register_active_job,
                    deregister_active_job=plane.deregister_active_job,
                    lease_renewer=plane.lease_renewer,
                    local_db=local_db,
                    coordinator=plane.coordinator,
                    executor=runtime.executor,
                    adb=adb,
                    job_runner_state=runtime.job_runner_state,
                    mq_producer=mq_producer,
                    script_registry=script_registry,
                    patrol_checkpoint_store=patrol_checkpoint_store,
                    operation_scheduler=plane.operation_scheduler,
                    step_trace_uploader=runtime.step_trace_uploader,
                    run_task_wrapper=run_task_wrapper,
                )
            except Exception:
                logger.exception("agent_loop_failed", extra={"host_id": host_id})
            # Use event wait instead of sleep so SIGTERM wakes us immediately
            shutdown_event.wait(poll_interval)
    finally:
        shutdown_agent_runtime(
            coordinator=plane.coordinator,
            operation_scheduler=plane.operation_scheduler,
            job_runner_state=runtime.job_runner_state,
            executor=runtime.executor,
            step_trace_uploader=runtime.step_trace_uploader,
            outbox_drain=runtime.outbox_drain,
            log_signal_drainer=log_signal_drainer,
            recovery_sync_stop=runtime.recovery_sync_stop,
            recovery_sync_thread=runtime.recovery_sync_thread,
            heartbeat_thread=heartbeat_thread,
            lease_renewer=plane.lease_renewer,
            mq_producer=mq_producer,
            local_db=local_db,
            sio_client=sio_client,
        )
