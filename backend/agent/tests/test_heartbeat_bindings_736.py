"""#736: heartbeat wiring extracted from main."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

from backend.agent.heartbeat_bindings import (
    RecoveryActionsSlot,
    build_active_count_getters,
    build_heartbeat_thread,
)


def test_active_count_getters():
    lock = threading.Lock()
    jobs = {1, 2}
    devices = {10}
    get_jobs, get_devs = build_active_count_getters(lock, jobs, devices)
    assert get_jobs() == 2
    assert get_devs() == 1
    jobs.add(3)
    assert get_jobs() == 3


def test_build_heartbeat_thread_reconnect_uses_slot():
    slot = RecoveryActionsSlot()
    execute = MagicMock(return_value=True)
    local_db = MagicMock()
    script_registry = MagicMock()
    script_registry.version = 1
    mq = MagicMock()

    with (
        patch("backend.agent.heartbeat_bindings.HeartbeatThread") as ht_cls,
        patch(
            "backend.agent.heartbeat_bindings.trigger_recovery_sync_on_device_reconnect"
        ) as trigger,
    ):
        ht_cls.return_value = MagicMock()
        build_heartbeat_thread(
            api_url="http://x",
            host_id="h",
            adb_path="adb",
            mount_points=[],
            host_info={},
            poll_interval=5.0,
            sio_client=MagicMock(),
            script_registry=script_registry,
            local_db=local_db,
            mq_producer=mq,
            agent_instance_id="inst",
            boot_id="boot",
            agent_version="1.0.0",
            agent_code_revision="rev",
            active_jobs_lock=threading.Lock(),
            active_job_ids=set(),
            active_device_ids=set(),
            recovery_actions_slot=slot,
        )
        kwargs = ht_cls.call_args.kwargs
        # before slot filled → no-op False
        assert kwargs["on_devices_reconnected"](["S"]) is False
        trigger.assert_not_called()
        slot.value = execute
        assert kwargs["on_devices_reconnected"](["S"]) is True
        trigger.assert_called_once()
        assert trigger.call_args.kwargs["execute_actions"] is execute


def test_main_wires_heartbeat_builder():
    import backend.agent.main as agent_main
    from tools.dev.source_anchor import SourceGuard

    guard = SourceGuard.of_module(agent_main).anchored("build_heartbeat_thread(")
    guard.assert_absent(
        "scan_shard_register_failure_total",
        why="#736 heartbeat outbox 字面量已迁出 main",
    )
    guard.assert_absent(
        "HeartbeatThread(",
        why="#736 HeartbeatThread 构造已迁出 main",
    )
