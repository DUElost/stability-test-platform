"""#736: agent claim-loop shell extracted from main."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

from backend.agent.agent_loop import run_agent_loop
from backend.agent.host_control_plane import HostControlPlane
from backend.agent.job_runtime import JobRuntime


def test_run_agent_loop_ticks_then_shuts_down_on_signal():
    plane = HostControlPlane(
        operation_scheduler=MagicMock(),
        coordinator=MagicMock(),
        occupancy=MagicMock(),
        job_runner_slot=MagicMock(),
        lease_renewer=MagicMock(),
        register_active_job=MagicMock(),
        deregister_active_job=MagicMock(),
    )
    runtime = JobRuntime(
        outbox_drain=MagicMock(),
        executor=MagicMock(),
        job_runner_state=MagicMock(),
        step_trace_uploader=MagicMock(),
        recovery_sync_stop=threading.Event(),
        recovery_sync_thread=MagicMock(),
        execute_recovery_actions=MagicMock(),
    )
    tick_count = {"n": 0}

    def _tick(**kwargs):
        tick_count["n"] += 1
        # Fire SIGTERM path by setting the shutdown event via the patched wait
        raise_stop.set()

    raise_stop = threading.Event()

    with (
        patch("backend.agent.agent_loop.process_claim_tick", side_effect=_tick),
        patch("backend.agent.agent_loop.shutdown_agent_runtime") as shutdown,
        patch("backend.agent.agent_loop.signal.signal"),
        patch(
            "backend.agent.agent_loop.threading.Event",
            side_effect=lambda: raise_stop,
        ),
    ):
        # First Event() is shutdown_event; make wait return after one tick
        raise_stop.clear()

        def _wait(timeout=None):
            return True  # pretend signal arrived

        raise_stop.wait = _wait  # type: ignore[method-assign]

        run_agent_loop(
            api_url="http://x",
            poll_interval=0.01,
            host_id="h",
            agent_instance_id="inst",
            plane=plane,
            runtime=runtime,
            heartbeat_thread=MagicMock(),
            adb=MagicMock(),
            mq_producer=MagicMock(),
            script_registry=MagicMock(),
            patrol_checkpoint_store=MagicMock(),
            run_task_wrapper=MagicMock(),
            log_signal_drainer=MagicMock(),
            local_db=MagicMock(),
            sio_client=MagicMock(),
        )

    assert tick_count["n"] >= 1
    shutdown.assert_called_once()
    assert shutdown.call_args.kwargs["coordinator"] is plane.coordinator
    assert shutdown.call_args.kwargs["executor"] is runtime.executor


def test_main_wires_agent_loop():
    import backend.agent.main as agent_main
    from tools.dev.source_anchor import SourceGuard

    guard = SourceGuard.of_module(agent_main).anchored("run_agent_loop(")
    guard.assert_absent(
        "process_claim_tick(",
        why="#736 claim tick 调用已迁出 main",
    )
    guard.assert_absent(
        "shutdown_agent_runtime(",
        why="#736 shutdown 调用已迁出 main",
    )
    guard.assert_absent(
        "signal.signal(",
        why="#736 SIGTERM/SIGINT 注册已迁出 main",
    )
