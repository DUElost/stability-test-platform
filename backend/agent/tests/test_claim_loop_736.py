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
    import backend.agent.agent_loop as agent_loop
    from tools.dev.source_anchor import SourceGuard

    guard = SourceGuard.of_module(agent_loop).anchored("process_claim_tick(")
    guard.assert_absent(
        "pending_jobs_fetched",
        why="#736 claim_loop 已抽出，loop 外壳不得回潮 claim 日志字面量",
    )


def test_claim_tick_reports_active_job_ids_snapshot():
    kwargs = _tick_kwargs()
    with kwargs["occupancy"].lock:
        kwargs["occupancy"].job_ids.add(12)
    kwargs["local_db"].get_active_jobs.return_value = [{"job_id": 5}, {"job_id": 12}]
    with patch("backend.agent.claim_loop.fetch_pending_jobs", return_value=[]) as fetch:
        process_claim_tick(**kwargs)
    assert fetch.call_args.kwargs["active_job_ids"] == [5, 12]


def test_fetch_pending_jobs_active_job_ids_contract():
    from backend.agent.api_client import fetch_pending_jobs

    resp = MagicMock()
    resp.json.return_value = {"data": []}
    with patch("backend.agent.api_client.requests.post", return_value=resp) as post:
        fetch_pending_jobs("http://x", "h1", "inst", capacity=1)
        assert "active_job_ids" not in post.call_args.kwargs["json"]
        assert post.call_args.kwargs["timeout"] == (5, 20)

        fetch_pending_jobs("http://x", "h1", "inst", capacity=1, active_job_ids=[9, 3, 9])
        assert post.call_args.kwargs["json"]["active_job_ids"] == [3, 9]


def test_claim_tick_respects_reload_zeroed_slots_across_vbus_probe():
    """#4077 R3 B1: claim tick 取 VBUS 名额期间若切根归零普通槽位，fetch_pending_jobs 仅带 capacity=0 + vbus_slots=1。"""
    kwargs = _tick_kwargs()
    kwargs["heartbeat_thread"].effective_slots = 2

    def fake_vbus_slots() -> int:
        # 模拟并发 reload_config ACK 将心跳普通有效槽位归零
        kwargs["heartbeat_thread"].effective_slots = 0
        return 1

    with patch("backend.agent.claim_loop.vbus_claim_slots", side_effect=fake_vbus_slots), patch(
        "backend.agent.claim_loop.fetch_pending_jobs", return_value=[]
    ) as fetch:
        process_claim_tick(**kwargs)

    fetch.assert_called_once()
    assert fetch.call_args.kwargs["capacity"] == 0
    assert fetch.call_args.kwargs["vbus_slots"] == 1
