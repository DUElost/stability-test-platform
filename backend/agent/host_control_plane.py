"""Host scheduler / coordinator / lease plane extracted from ``main`` (#736).

Starts OperationScheduler + HostRunCoordinator (late-bound onto heartbeat),
then occupancy + LeaseRenewer + register/deregister active-job helpers.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Callable, Dict, Set

from .active_job_bindings import (
    ActiveJobOccupancy,
    JobRunnerStateSlot,
    build_deregister_active_job,
    build_on_lease_lost,
    build_register_active_job,
)
from .control_handler import ControlHandlerDeps
from .coordinator import HostRunCoordinator
from .lease_renewer import LeaseRenewer
from .operation_scheduler import OperationScheduler


@dataclass
class HostControlPlane:
    """Running host-global control plane after heartbeat has started."""

    operation_scheduler: OperationScheduler
    coordinator: HostRunCoordinator
    occupancy: ActiveJobOccupancy
    job_runner_slot: JobRunnerStateSlot
    lease_renewer: LeaseRenewer
    register_active_job: Callable[..., None]
    deregister_active_job: Callable[..., None]


def start_host_control_plane(
    *,
    api_url: str,
    host_id: str,
    agent_instance_id: str,
    agent_secret: str,
    local_db: Any,
    heartbeat_thread: Any,
    control_deps: ControlHandlerDeps,
    active_jobs_lock: threading.Lock,
    active_job_ids: Set[int],
    active_device_ids: Set[int],
    active_job_tokens: Dict[int, str],
    active_device_owner: Dict[int, int],
    lock_renewal_stop_event: threading.Event,
) -> HostControlPlane:
    """Wire and start scheduler, coordinator, and lease renewer."""
    # ADR-0026 Step 5b: create host-global scheduler + coordinator BEFORE
    # any component that references them (LeaseRenewer, claim loop, etc.).
    operation_scheduler = OperationScheduler()
    # Late-bind: HeartbeatThread starts before scheduler exists.
    heartbeat_thread._get_operation_stats = operation_scheduler.concurrency_snapshot
    coordinator = HostRunCoordinator(
        api_url,
        host_id,
        agent_instance_id,
        agent_secret=agent_secret,
        local_db=local_db,
    )
    # ADR-0026 Step 5b: wire scheduler to coordinator for abort/cancel
    coordinator.set_scheduler(operation_scheduler)
    # Start the per-host coordinator heartbeat (reports coordinator
    # heartbeats + per-job execution_state to control plane).
    coordinator.start()
    control_deps.coordinator = coordinator
    control_deps.operation_scheduler = operation_scheduler
    control_deps.heartbeat_thread = heartbeat_thread

    occupancy = ActiveJobOccupancy(
        lock=active_jobs_lock,
        job_ids=active_job_ids,
        device_ids=active_device_ids,
        job_tokens=active_job_tokens,
        device_owner=active_device_owner,
    )
    job_runner_slot = JobRunnerStateSlot()
    # ADR-0019 Phase 3b: lease 丢失回调（409 时 LeaseRenewer 内部已清理，
    # 此处处理外部状态）
    on_lease_lost = build_on_lease_lost(
        occupancy=occupancy,
        job_runner_slot=job_runner_slot,
        coordinator=coordinator,
        local_db=local_db,
    )

    lease_renewer = LeaseRenewer(
        api_url,
        active_jobs_lock=active_jobs_lock,
        active_job_ids=active_job_ids,
        lock_renewal_stop_event=lock_renewal_stop_event,
        agent_instance_id=agent_instance_id,
        on_lease_lost=on_lease_lost,
        host_id=host_id,
        coordinator=coordinator,
    )
    lease_renewer.start()

    # ADR-0019 Phase 2b + Phase 3a/3b: 活跃 job 注册/注销
    register_active_job = build_register_active_job(
        occupancy=occupancy,
        lease_renewer=lease_renewer,
        local_db=local_db,
    )
    deregister_active_job = build_deregister_active_job(
        occupancy=occupancy,
        lease_renewer=lease_renewer,
        local_db=local_db,
    )

    return HostControlPlane(
        operation_scheduler=operation_scheduler,
        coordinator=coordinator,
        occupancy=occupancy,
        job_runner_slot=job_runner_slot,
        lease_renewer=lease_renewer,
        register_active_job=register_active_job,
        deregister_active_job=deregister_active_job,
    )
