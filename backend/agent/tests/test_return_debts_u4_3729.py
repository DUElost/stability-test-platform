"""Heartbeat debt execution with real scheduler, frozen script and finite clock."""
import threading
from types import SimpleNamespace

import pytest
import requests

from backend.agent.device_return import ReturnDebtConsumer
from backend.agent.heartbeat_bindings import RecoveryActionsSlot, build_heartbeat_thread
from backend.agent.pipeline_engine import PipelineEngine
from backend.agent.tests.test_return_init_u4_3729 import device_factory  # noqa: F401

DEVICES = [{"serial": "u4-test-device", "adb_connected": True}]


def heartbeat(token="debt-test-token"):
    return {"return_init_check_available": True, "return_debts": [{
        "device_id": 1, "serial": "u4-test-device", "fencing_token": token,
        "families": ["power-cycle"],
    }]}


def consumer_for(device, *, active=lambda _: False):
    return ReturnDebtConsumer(api_url="http://u4-test.invalid", adb_path="test-only",
        script_registry=device.registry, local_db=device.local_db, get_scheduler=lambda: device.scheduler,
        is_active=active, engine_factory=PipelineEngine, clock=lambda: device.clock, launch=lambda fn: fn())


def test_first_token_then_physical_reconnect_each_executes_once(device_factory):
    device = device_factory()
    consumer = consumer_for(device)
    consumer.receive(heartbeat(), DEVICES)
    consumer.receive(heartbeat(), DEVICES)
    assert device.modes == ["disarm"]
    assert device.reports[0]["fencing_token"] == "debt-test-token"
    assert device.reports[0]["family_results"]["power-cycle"]["evidence"]["script_version"] == "1.0.8"
    consumer.receive(heartbeat(), [])
    consumer.receive(heartbeat(), DEVICES)
    consumer.reconnected(["u4-test-device"])
    consumer.reconnected(["u4-test-device"])
    assert device.modes == ["disarm", "disarm"]
    assert device.scheduler.held == 0


def test_failed_discharge_waits_full_30_minutes(device_factory):
    device = device_factory(kind="denied")
    consumer = consumer_for(device)
    consumer.receive(heartbeat(), DEVICES)
    assert device.reports[0]["family_results"]["power-cycle"]["result"] == "failed"
    device.advance(1799)
    consumer.receive(heartbeat(), DEVICES)
    assert len(device.modes) == 1
    device.advance(1)
    consumer.receive(heartbeat(), DEVICES)
    assert len(device.modes) == 2


def test_unverified_does_not_retry_periodically_but_new_token_does(device_factory):
    device = device_factory(trusted=False)
    consumer = consumer_for(device)
    consumer.receive(heartbeat(), DEVICES)
    assert device.reports[0]["family_results"]["power-cycle"]["result"] == "executed_unverified"
    device.advance(1800)
    consumer.receive(heartbeat(), DEVICES)
    assert device.modes == ["disarm"]
    consumer.receive(heartbeat("new-debt-test-token"), DEVICES)
    assert device.modes == ["disarm", "disarm"]


def test_unverified_reconnect_retries_once_even_if_recovery_callback_repeats(device_factory):
    device = device_factory(trusted=False)
    consumer = consumer_for(device)
    consumer.receive(heartbeat(), DEVICES)
    consumer.receive(None, [])  # lost HTTP response still records physical disconnect
    consumer.receive(heartbeat(), DEVICES)
    for _ in range(3):
        consumer.reconnected(["u4-test-device"])
    assert device.modes == ["disarm", "disarm"]


def test_unreported_discharge_retries_only_after_30_minutes(device_factory):
    device = device_factory()
    device.report_statuses = [requests.ConnectionError("test outage") for _ in range(3)]
    consumer = consumer_for(device)
    consumer.receive(heartbeat(), DEVICES)
    assert len(device.reports) == 3 and device.modes == ["disarm"]
    device.advance(1799)
    consumer.receive(heartbeat(), DEVICES)
    assert len(device.modes) == 1
    device.advance(1)
    consumer.receive(heartbeat(), DEVICES)
    assert len(device.modes) == 2 and len(device.reports) == 4


@pytest.mark.parametrize("status", [409, 422])
def test_definitive_rejection_does_not_automatically_retry(device_factory, status):
    device = device_factory()
    device.report_statuses = [status]
    consumer = consumer_for(device)
    consumer.receive(heartbeat(), DEVICES)
    assert len(device.reports) == 1
    device.advance(1800)
    consumer.receive(heartbeat(), DEVICES)
    consumer.reconnected(["u4-test-device"])
    assert len(device.reports) == 1
    if status == 409:
        assert not consumer._debts
        consumer.receive(heartbeat("replacement-test-token"), DEVICES)
        assert len(device.reports) == 2


def test_active_job_blocks_discharge_including_after_permit_acquisition(device_factory):
    device = device_factory()
    active = [True]
    consumer = consumer_for(device, active=lambda _: active[0])
    consumer.receive(heartbeat(), DEVICES)
    assert device.calls == [] and device.reports == []
    active[0] = False
    consumer.receive(heartbeat(), DEVICES)
    assert device.modes == ["disarm"]
    checks = []

    def becomes_active(_):
        checks.append(True)
        return len(checks) > 1
    other = consumer_for(device, active=becomes_active)
    other.receive(heartbeat("race-test-token"), DEVICES)
    assert len(checks) == 2 and device.modes == ["disarm"]
    assert device.scheduler.held == 0


def test_no_permit_prevents_execution_and_retries_when_slot_is_free(device_factory):
    device = device_factory()
    consumer = consumer_for(device)
    with device.scheduler.acquire(99):
        consumer.receive(heartbeat(), DEVICES)
        assert device.modes == [] and device.reports == []
    consumer.receive(heartbeat(), DEVICES)
    assert device.modes == ["disarm"] and device.scheduler.held == 0


def test_missing_field_clears_cached_debt_and_disables_init_reporting(device_factory):
    device = device_factory(trusted=False)
    consumer = consumer_for(device)
    consumer.receive({}, DEVICES)
    consumer.reconnected(["u4-test-device"])
    assert device.modes == []
    consumer.receive(heartbeat(), DEVICES)
    assert len(device.modes) == 1
    consumer.receive({}, [])
    consumer.receive({}, DEVICES)
    consumer.reconnected(["u4-test-device"])
    assert len(device.modes) == 1
    assert device.local_db.get_state("return_init_check_available") == "0"


def test_real_heartbeat_bindings_use_host_scheduler_owner_and_local_occupancy(device_factory):
    device = device_factory()
    active_devices: set[int] = set()
    thread = build_heartbeat_thread(api_url="http://u4-test.invalid", host_id="test-host",
        adb_path="test-only", mount_points=[], host_info={}, poll_interval=5,
        sio_client=None, script_registry=device.registry, local_db=device.local_db,
        mq_producer=SimpleNamespace(set_log_rate_limit=lambda *_: None), agent_instance_id="test-instance",
        boot_id="test-boot", agent_version="test-only", agent_code_revision="test-only",
        active_jobs_lock=threading.Lock(), active_job_ids=set(), active_device_ids=active_devices,
        recovery_actions_slot=RecoveryActionsSlot())
    consumer = thread._on_return_state.__self__
    consumer.launch = lambda fn: fn()
    thread._on_return_state(heartbeat(), DEVICES)
    assert device.modes == []  # host scheduler has not been bound yet
    # This is the existing production late binding in start_host_control_plane.
    thread._get_operation_stats = device.scheduler.concurrency_snapshot
    thread._on_return_state(heartbeat(), DEVICES)
    assert device.modes == ["disarm"]
    active_devices.add(1)
    thread._on_return_state(heartbeat("next-test-token"), DEVICES)
    assert device.modes == ["disarm"]
    active_devices.clear()
    device.local_db.save_active_job(99, 1, "recovered-test-token")
    thread._on_return_state(heartbeat("next-test-token"), DEVICES)
    assert device.modes == ["disarm"]
    device.local_db.delete_active_job(99)
    thread._on_return_state(heartbeat("next-test-token"), DEVICES)
    assert device.modes == ["disarm", "disarm"]
    assert thread._on_devices_reconnected(["u4-test-device"]) is False
    assert device.modes == ["disarm", "disarm"]


def test_real_heartbeat_tick_delivers_debt_and_reconnect_hook(device_factory, monkeypatch):
    from backend.agent import heartbeat_thread as module, heartbeat as heartbeat_module, system_monitor

    device = device_factory()
    thread = build_heartbeat_thread(api_url="http://u4-test.invalid", host_id="test-host",
        adb_path="test-only", mount_points=[], host_info={}, poll_interval=5,
        sio_client=None, script_registry=device.registry, local_db=device.local_db,
        mq_producer=SimpleNamespace(set_log_rate_limit=lambda *_: None), agent_instance_id="test-instance",
        boot_id="test-boot", agent_version="test-only", agent_code_revision="test-only",
        active_jobs_lock=threading.Lock(), active_job_ids=set(), active_device_ids=set(),
        recovery_actions_slot=RecoveryActionsSlot())
    thread._get_operation_stats = device.scheduler.concurrency_snapshot
    thread._on_return_state.__self__.launch = lambda fn: fn()
    connected = [False]
    monkeypatch.setattr(module, "send_heartbeat", lambda *a, **k: heartbeat())
    monkeypatch.setattr(module.device_discovery, "discover_devices", lambda *_: [{
        "serial": "u4-test-device", "adb_state": "device", "model": device.model}])
    monkeypatch.setattr(thread, "_collect_device_infos", lambda _: [{
        "adb_connected": connected[0], "adb_state": "device", "platform": "MTK"}])
    monkeypatch.setattr(thread, "_maybe_sample_disk", lambda *a: 0)
    monkeypatch.setattr(thread, "_maybe_auto_reconnect_offline", lambda *a: None)
    monkeypatch.setattr(module.device_discovery, "list_adb_fork_servers", lambda: [])
    monkeypatch.setattr(module.device_discovery, "count_usb_devices_and_root_hubs", lambda: (1, 1))
    monkeypatch.setattr(module.device_discovery, "count_adb_interface_devices", lambda: 1)
    monkeypatch.setattr(system_monitor, "collect_system_stats", lambda: {})
    monkeypatch.setattr(heartbeat_module, "check_mounts", lambda _: {})
    thread._kernel_usb_watch = SimpleNamespace(poll=lambda **k: [], channel_state=lambda: None)
    thread._get_outbox_counts = thread._get_archive_metrics = None
    thread._agent_artifact_digest = ""
    thread._tick()
    assert device.modes == []
    connected[0] = True
    thread._tick()
    thread._tick()  # recovery's repeated pending reconnect is not another device reconnect
    assert device.modes == ["disarm"]
    assert device.reports[0]["fencing_token"] == "debt-test-token"
    assert device.local_db.get_state("return_init_check_available") == "1"
