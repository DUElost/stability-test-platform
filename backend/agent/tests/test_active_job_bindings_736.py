"""#736: active job occupancy bindings extracted from main."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

from backend.agent.active_job_bindings import (
    ActiveJobOccupancy,
    JobRunnerStateSlot,
    build_deregister_active_job,
    build_on_lease_lost,
    build_register_active_job,
)


def _occupancy():
    return ActiveJobOccupancy(
        lock=threading.Lock(),
        job_ids=set(),
        device_ids=set(),
        job_tokens={},
        device_owner={},
    )


def test_register_active_job_occupies_memory_and_persists():
    occ = _occupancy()
    lease = MagicMock()
    local_db = MagicMock()
    register = build_register_active_job(
        occupancy=occ, lease_renewer=lease, local_db=local_db
    )

    register(7, "tok", 70, "SERIAL", local_worker_token="worker-7")

    assert 7 in occ.job_ids
    assert occ.job_tokens[7] == "worker-7"
    assert 70 in occ.device_ids
    assert occ.device_owner[70] == 7
    lease.set_fencing_token.assert_called_once_with(7, "tok", 70, "worker-7")
    local_db.save_active_job.assert_called_once_with(7, 70, "tok", "SERIAL")


def test_deregister_active_job_clears_occupancy_on_normal_exit():
    occ = _occupancy()
    occ.job_ids.add(7)
    occ.job_tokens[7] = "tok"
    occ.device_ids.add(70)
    occ.device_owner[70] = 7
    lease = MagicMock()
    lease.clear_fencing_token_if_current.return_value = 70
    local_db = MagicMock()
    local_db.has_terminal_fact.return_value = True
    deregister = build_deregister_active_job(
        occupancy=occ, lease_renewer=lease, local_db=local_db
    )

    deregister(7, "tok")

    assert 7 not in occ.job_ids
    assert 70 not in occ.device_ids
    local_db.delete_active_job.assert_called_once_with(7)


def test_on_lease_lost_uses_late_bound_job_runner_state():
    occ = _occupancy()
    occ.job_ids.add(7)
    occ.job_tokens[7] = "tok"
    occ.device_ids.add(70)
    occ.device_owner[70] = 7
    slot = JobRunnerStateSlot()
    runner_state = MagicMock()
    runner_state.request_abort.return_value = True
    slot.value = runner_state
    coordinator = MagicMock()
    on_lost = build_on_lease_lost(
        occupancy=occ,
        job_runner_slot=slot,
        coordinator=coordinator,
        local_db=MagicMock(),
    )

    on_lost(7, 70)

    runner_state.request_abort.assert_called_once_with(7)
    assert 7 not in occ.job_ids
    assert 70 in occ.device_ids  # keep slot while abort dispatched
    coordinator.cancel_waiting_job.assert_called_once_with(7)


def test_main_wires_active_job_bindings():
    import backend.agent.host_control_plane as plane
    import backend.agent.job_runtime as job_runtime
    from tools.dev.source_anchor import SourceGuard

    # 正锚点：占位工厂在 host_control_plane；slot 赋值在 job_runtime
    plane_guard = (
        SourceGuard.of_module(plane)
        .anchored("build_register_active_job(")
        .anchored("build_deregister_active_job(")
        .anchored("build_on_lease_lost(")
    )
    plane_guard.assert_absent(
        "def _register_active_job(",
        why="#736 active_job_bindings 已抽出，plane 不得回潮内联 register 闭包",
    )
    SourceGuard.of_module(job_runtime).anchored(
        "job_runner_slot.value = job_runner_state"
    )
