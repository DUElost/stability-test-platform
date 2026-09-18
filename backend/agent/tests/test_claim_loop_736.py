"""#736: claim tick extracted from main."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

from backend.agent.active_job_bindings import ActiveJobOccupancy
from backend.agent.claim_loop import process_claim_tick


def _occupancy():
    return ActiveJobOccupancy(
        lock=threading.Lock(),
        job_ids=set(),
        device_ids=set(),
        job_tokens={},
        device_owner={},
    )


def _tick_kwargs(**overrides):
    hb = MagicMock()
    hb.effective_slots = 2
    kwargs = dict(
        api_url="http://x",
        host_id="h1",
        agent_instance_id="inst",
        occupancy=_occupancy(),
        heartbeat_thread=hb,
        register_active_job=MagicMock(),
        deregister_active_job=MagicMock(),
        lease_renewer=MagicMock(),
        local_db=MagicMock(),
        coordinator=MagicMock(),
        executor=MagicMock(),
        adb=MagicMock(),
        job_runner_state=MagicMock(),
        mq_producer=MagicMock(),
        script_registry=MagicMock(),
        patrol_checkpoint_store=MagicMock(),
        operation_scheduler=MagicMock(),
        step_trace_uploader=MagicMock(),
        run_task_wrapper=MagicMock(),
    )
    kwargs.update(overrides)
    return kwargs


def test_claim_tick_noops_when_no_slots():
    kwargs = _tick_kwargs()
    kwargs["heartbeat_thread"].effective_slots = 0
    with patch("backend.agent.claim_loop.fetch_pending_jobs") as fetch:
        process_claim_tick(**kwargs)
    fetch.assert_not_called()


def test_claim_tick_registers_and_submits():
    kwargs = _tick_kwargs()
    job = {
        "id": 7,
        "device_id": 70,
        "fencing_token": "tok",
        "device_serial": "S",
        "plan_run_host_id": 1,
        "plan_run_id": 2,
    }
    with patch(
        "backend.agent.claim_loop.fetch_pending_jobs", return_value=[job]
    ):
        process_claim_tick(**kwargs)

    kwargs["register_active_job"].assert_called_once()
    kwargs["coordinator"].register_job.assert_called_once_with(7)
    kwargs["coordinator"].register_job_device.assert_called_once_with(7, 70)
    kwargs["executor"].submit.assert_called_once()
    assert 70 in kwargs["occupancy"].device_ids


def test_claim_tick_rolls_back_on_register_failure():
    kwargs = _tick_kwargs()
    kwargs["register_active_job"].side_effect = RuntimeError("sqlite")
    job = {"id": 7, "device_id": 70, "fencing_token": "tok", "device_serial": "S"}
    with (
        patch("backend.agent.claim_loop.fetch_pending_jobs", return_value=[job]),
        patch("backend.agent.claim_loop._rollback_failed_claim") as rollback,
    ):
        process_claim_tick(**kwargs)
    rollback.assert_called_once()
    kwargs["executor"].submit.assert_not_called()


def test_main_wires_claim_tick():
    import backend.agent.main as agent_main
    from tools.dev.source_anchor import SourceGuard

    guard = SourceGuard.of_module(agent_main).anchored("process_claim_tick(")
    guard.assert_absent(
        "pending_jobs_fetched",
        why="#736 claim_loop 已抽出，main 不得回潮 claim 日志字面量",
    )
