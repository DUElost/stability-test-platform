"""#736: host control plane extracted from main."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

from backend.agent.control_handler import ControlHandlerDeps
from backend.agent.host_control_plane import start_host_control_plane


def test_start_host_control_plane_wires_and_starts():
    heartbeat = MagicMock()
    control_deps = ControlHandlerDeps()
    lock = threading.Lock()
    job_ids: set[int] = set()
    device_ids: set[int] = set()
    tokens: dict[int, str] = {}
    owners: dict[int, int] = {}
    stop = threading.Event()

    with (
        patch("backend.agent.host_control_plane.OperationScheduler") as sched_cls,
        patch("backend.agent.host_control_plane.HostRunCoordinator") as coord_cls,
        patch("backend.agent.host_control_plane.LeaseRenewer") as lease_cls,
        patch("backend.agent.host_control_plane.build_on_lease_lost") as on_lost,
        patch("backend.agent.host_control_plane.build_register_active_job") as reg,
        patch("backend.agent.host_control_plane.build_deregister_active_job") as dereg,
    ):
        sched = MagicMock()
        sched.concurrency_snapshot = MagicMock()
        sched_cls.return_value = sched
        coord = MagicMock()
        coord_cls.return_value = coord
        lease = MagicMock()
        lease_cls.return_value = lease
        reg.return_value = MagicMock(name="register")
        dereg.return_value = MagicMock(name="deregister")
        on_lost.return_value = MagicMock(name="on_lost")

        plane = start_host_control_plane(
            api_url="http://x",
            host_id="h",
            agent_instance_id="inst",
            agent_secret="s",
            local_db=MagicMock(),
            heartbeat_thread=heartbeat,
            control_deps=control_deps,
            active_jobs_lock=lock,
            active_job_ids=job_ids,
            active_device_ids=device_ids,
            active_job_tokens=tokens,
            active_device_owner=owners,
            lock_renewal_stop_event=stop,
        )

    assert heartbeat._get_operation_stats is sched.concurrency_snapshot
    coord.set_scheduler.assert_called_once_with(sched)
    coord.start.assert_called_once()
    lease.start.assert_called_once()
    assert control_deps.coordinator is coord
    assert control_deps.operation_scheduler is sched
    assert control_deps.heartbeat_thread is heartbeat
    assert plane.coordinator is coord
    assert plane.lease_renewer is lease
    assert plane.register_active_job is reg.return_value
    assert plane.deregister_active_job is dereg.return_value


def test_main_wires_host_control_plane():
    import backend.agent.agent_application as app
    from tools.dev.source_anchor import SourceGuard

    guard = SourceGuard.of_module(app).anchored("start_host_control_plane(")
    guard.assert_absent(
        "OperationScheduler(",
        why="#736 scheduler 构造已迁出 main",
    )
    guard.assert_absent(
        "LeaseRenewer(",
        why="#736 lease 续租器构造已迁出 main",
    )
