"""#736: graceful shutdown extracted from main."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from backend.agent.graceful_shutdown import shutdown_agent_runtime


def test_shutdown_stops_singletons_outside_watcher_branch():
    src = (
        Path(__file__).resolve().parents[1] / "graceful_shutdown.py"
    ).read_text(encoding="utf-8")
    body = src[src.find("def shutdown_agent_runtime") :]
    eu = body.find("EventUploader.instance().stop")
    arch = body.find("LogArchiver.instance().stop")
    disk = body.find("LocalDiskMonitor.instance().stop")
    drain_branch = body.find("if log_signal_drainer is not None:")
    assert eu > 0 and arch > 0 and disk > 0 and drain_branch > 0
    assert eu < drain_branch
    assert arch < drain_branch
    assert disk < drain_branch


def test_shutdown_agent_runtime_order():
    coordinator = MagicMock()
    scheduler = MagicMock()
    runner = MagicMock()
    runner.active_runners = {1: object()}
    executor = MagicMock()
    step = MagicMock()
    step.drain_sync.return_value = 0
    outbox = MagicMock()
    outbox.drain_sync.return_value = 0
    recovery_stop = MagicMock()
    recovery_thread = MagicMock()
    heartbeat = MagicMock()
    lease = MagicMock()
    mq = MagicMock()
    local_db = MagicMock()
    sio = MagicMock()

    with (
        patch("backend.agent.graceful_shutdown.LocalDiskMonitor") as disk,
        patch("backend.agent.graceful_shutdown.LogArchiver") as arch,
        patch("backend.agent.graceful_shutdown.EventUploader") as eu,
    ):
        shutdown_agent_runtime(
            coordinator=coordinator,
            operation_scheduler=scheduler,
            job_runner_state=runner,
            executor=executor,
            step_trace_uploader=step,
            outbox_drain=outbox,
            log_signal_drainer=None,
            recovery_sync_stop=recovery_stop,
            recovery_sync_thread=recovery_thread,
            heartbeat_thread=heartbeat,
            lease_renewer=lease,
            mq_producer=mq,
            local_db=local_db,
            sio_client=sio,
        )

    coordinator.stop.assert_called_once()
    scheduler.shutdown.assert_called_once()
    runner.request_abort.assert_called_once_with(1)
    executor.shutdown.assert_called_once_with(wait=True, cancel_futures=False)
    disk.instance.return_value.stop.assert_called_once()
    arch.instance.return_value.stop.assert_called_once()
    eu.instance.return_value.stop.assert_called_once()
    recovery_stop.set.assert_called_once()
    heartbeat.stop.assert_called_once()
    lease.stop.assert_called_once()
    mq.close.assert_called_once()
    local_db.close.assert_called_once()
    sio.disconnect.assert_called_once()


def test_main_wires_shutdown_helper():
    import backend.agent.agent_loop as agent_loop
    from tools.dev.source_anchor import SourceGuard

    guard = SourceGuard.of_module(agent_loop).anchored("shutdown_agent_runtime(")
    guard.assert_absent(
        "agent_shutting_down",
        why="#736 graceful_shutdown 已抽出，loop 外壳不得回潮停机日志字面量",
    )
    guard.assert_absent(
        "EventUploader.instance().stop",
        why="#736 停机单例 stop 已迁出 loop 外壳",
    )
