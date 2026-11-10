# -*- coding: utf-8 -*-
"""#3918 / ADR-0063 D7：vbus 槽位 USB 在位观测（Agent 上报 + 共享分类）。"""

import json

import pytest

from backend.agent.capacity_reporter import compute_capacity
from backend.agent.contracts.usb_presence import (
    STATE_ABSENT,
    STATE_ADB_MODE,
    STATE_OTHER,
    STATE_POWERED_OFF,
    classify_usb_presence,
)
from backend.agent.usb_power_slots import read_usb_power_presence


def _slots_file(tmp_path, slots, schema=1):
    path = tmp_path / "usb-power-slots.json"
    path.write_text(json.dumps({"schema": schema, "slots": slots}), encoding="utf-8")
    return str(path)


def _usb_dev(sysfs, name, vid, pid):
    d = sysfs / name
    d.mkdir(parents=True)
    (d / "idVendor").write_text(vid + "\n", encoding="utf-8")
    (d / "idProduct").write_text(pid + "\n", encoding="utf-8")


@pytest.mark.parametrize("vid_pid,state", [
    ("0e8d:2046", STATE_POWERED_OFF),
    ("0E8D:2046", STATE_POWERED_OFF),
    ("0e8d:201c", STATE_ADB_MODE),
    ("0e8d:0003", STATE_OTHER),
    (None, STATE_ABSENT),
    ("", STATE_ABSENT),
])
def test_classify(vid_pid, state):
    assert classify_usb_presence(vid_pid) == state


def test_reads_each_mapped_port(tmp_path):
    sysfs = tmp_path / "sys"
    _usb_dev(sysfs, "1-5.2.1", "0e8d", "2046")
    _usb_dev(sysfs, "1-5.2.3", "0e8d", "201c")
    path = _slots_file(tmp_path, [
        {"serial": "A", "hub_location": "1-5.2", "port": 1},
        {"serial": "B", "hub_location": "1-5.2", "port": 3},
        {"serial": "C", "hub_location": "1-5.2", "port": 4},
    ])
    assert read_usb_power_presence(path, str(sysfs)) == {
        "A": ["1-5.2:1", "0e8d:2046"],
        "B": ["1-5.2:3", "0e8d:201c"],
        "C": ["1-5.2:4", None],
    }


def test_missing_mapping_file_reports_nothing(tmp_path):
    assert read_usb_power_presence(str(tmp_path / "absent.json"), str(tmp_path)) is None


@pytest.mark.parametrize("body", [
    "not json",
    json.dumps({"schema": 2, "slots": []}),
    json.dumps({"schema": 1, "slots": [{"serial": "A", "hub_location": "../x", "port": 1}]}),
    json.dumps({"schema": 1, "slots": [{"serial": "A", "hub_location": "1-5", "port": True}]}),
    json.dumps({"schema": 1, "slots": ["A"]}),
])
def test_malformed_mapping_reports_nothing(tmp_path, body):
    path = tmp_path / "usb-power-slots.json"
    path.write_text(body, encoding="utf-8")
    assert read_usb_power_presence(str(path), str(tmp_path)) is None


def test_garbage_sysfs_value_reads_as_absent(tmp_path):
    sysfs = tmp_path / "sys"
    _usb_dev(sysfs, "1-5.1", "zz", "2046")
    path = _slots_file(tmp_path, [{"serial": "A", "hub_location": "1-5", "port": 1}])
    assert read_usb_power_presence(path, str(sysfs)) == {"A": ["1-5:1", None]}


def _cap(**kw):
    return compute_capacity(
        active_job_count=0, active_device_count=0, online_healthy_devices=0, total_devices=0,
        system_stats={}, mount_status={}, **kw)["capacity"]


def test_capacity_key_only_when_mapping_present():
    assert "usb_power_slots" not in _cap()
    presence = {"A": ["1-5.2:1", "0e8d:2046"]}
    cap = _cap(usb_power_slots=presence)
    assert cap["usb_power_slots"] == presence
    assert cap["effective_slots"] == 0, "observation only: must not change slot math"


@pytest.mark.parametrize("presence,raises", [
    ({"A": ["1-5.2:1", "0e8d:2046"]}, False),
    (None, False),
    (None, True),
])
def test_heartbeat_tick_passes_presence_to_capacity(monkeypatch, presence, raises):
    from backend.agent.heartbeat_thread import HeartbeatThread

    cap_kwargs = {}

    def fake_compute_capacity(**kwargs):
        cap_kwargs.update(kwargs)
        return {"capacity": {"effective_slots": 0}, "health": {"status": "HEALTHY", "reasons": []}}

    def fake_presence():
        if raises:
            raise OSError("boom")
        return presence

    monkeypatch.setattr("backend.agent.heartbeat_thread.send_heartbeat", lambda *a, **k: {"ok": True})
    monkeypatch.setattr("backend.agent.heartbeat_thread.device_discovery.discover_devices", lambda adb: [])
    monkeypatch.setattr("backend.agent.heartbeat_thread.device_discovery.get_adb_server_port", lambda: 5037)
    monkeypatch.setattr("backend.agent.heartbeat_thread.device_discovery.list_adb_fork_servers", lambda: [])
    monkeypatch.setattr("backend.agent.capacity_reporter.compute_capacity", fake_compute_capacity)
    monkeypatch.setattr("backend.agent.heartbeat_thread.read_usb_power_presence", fake_presence)

    thread = HeartbeatThread(api_url="http://server", host_id="host-1", adb_path="adb", mount_points=[],
                             host_info={}, poll_interval=60, get_active_job_count=lambda: 0)
    thread._tick()
    assert cap_kwargs["usb_power_slots"] == presence
