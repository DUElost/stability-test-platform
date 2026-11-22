"""B10 U1 / ADR-0064 D5：心跳非本地 FS 非阻塞隔离。"""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

from backend.agent.mount_health_probe import (
    MountHealthProbe,
    parse_mountinfo_targets,
    path_is_mounted,
    unescape_mount_path,
)
from backend.agent.heartbeat_thread import HeartbeatThread


def test_unescape_and_parse_mountinfo_space_and_missing():
    text = (
        "36 35 98:0 / /mnt/stp\\040aee rw,relatime - nfs4 server:/export rw\n"
        "37 35 8:1 / /home rw,relatime - ext4 /dev/sda1 rw\n"
    )
    targets = parse_mountinfo_targets(text)
    assert "/mnt/stp aee" in targets
    assert "/home" in targets
    assert path_is_mounted("/mnt/stp aee", targets) is True
    assert path_is_mounted("/mnt/not-mounted", targets) is False
    assert unescape_mount_path(r"a\040b\011c") == "a b\tc"


def test_heartbeat_tick_bounded_when_probe_hangs(monkeypatch):
    """模拟可达性探针挂起 30s：_tick 有界返回且 mount_status.probe_stale=true。"""
    hang = threading.Event()
    released = threading.Event()

    def hanging_probe(_point: str) -> bool:
        hang.set()
        # 30s hang（测试里用短等 + 不释放，由 daemon 线程在进程结束时回收）
        released.wait(30.0)
        return True

    mountinfo = (
        "1 0 0:0 / /mnt/center rw - nfs4 server:/export rw\n"
    )
    sent = {}

    def fake_send(*_a, **kwargs):
        sent["kwargs"] = kwargs
        return {"ok": True}

    monkeypatch.setattr(
        "backend.agent.heartbeat_thread.send_heartbeat", fake_send
    )
    monkeypatch.setattr(
        "backend.agent.heartbeat_thread.device_discovery.discover_devices",
        lambda *_: [],
    )
    monkeypatch.setattr(
        "backend.agent.heartbeat_thread.device_discovery.list_adb_fork_servers",
        lambda: [],
    )
    monkeypatch.setattr(
        "backend.agent.heartbeat_thread.device_discovery.get_adb_server_port",
        lambda: 5037,
    )
    monkeypatch.setattr(
        "backend.agent.heartbeat_thread.device_discovery.count_usb_devices_and_root_hubs",
        lambda: (0, 1),
    )
    monkeypatch.setattr(
        "backend.agent.heartbeat_thread.device_discovery.count_adb_interface_devices",
        lambda: 0,
    )
    monkeypatch.setattr(
        "backend.agent.system_monitor.collect_system_stats",
        lambda: {"cpu": 1},
    )
    monkeypatch.setattr(
        "backend.agent.capacity_reporter.compute_capacity",
        lambda **kwargs: {
            "capacity": {"effective_slots": 0},
            "health": {"status": "HEALTHY", "reasons": []},
        },
    )

    thread = HeartbeatThread(
        api_url="http://test.invalid",
        host_id="host-u1",
        adb_path="adb",
        mount_points=["/mnt/center"],
        host_info={},
        poll_interval=60,
    )
    thread._mount_health_probe = MountHealthProbe(
        interval_seconds=0.0,
        stale_after_seconds=0.0,
        probe_fn=hanging_probe,
        mountinfo_reader=lambda _p: mountinfo,
    )
    thread._kernel_usb_watch = SimpleNamespace(
        poll=lambda **_k: [],
        channel_state=lambda: "ok",
    )

    started = time.monotonic()
    thread._tick()
    elapsed = time.monotonic() - started

    # 有界：远小于探针 30s 挂起
    assert elapsed < 5.0, f"_tick blocked for {elapsed:.2f}s"
    assert "kwargs" in sent, "HTTP heartbeat must be sent"
    mount_status = sent["kwargs"]["mount_status"]
    assert "/mnt/center" in mount_status
    assert mount_status["/mnt/center"]["probe_stale"] is True
    assert mount_status["/mnt/center"]["mounted"] is True
    # 探针线程已启动（证明主循环未同步等待）
    assert hang.wait(1.0) is True
    released.set()


def test_mount_health_probe_caches_reachable(monkeypatch):
    calls = []

    def probe(point: str) -> bool:
        calls.append(point)
        return True

    probe_obj = MountHealthProbe(
        interval_seconds=60.0,
        stale_after_seconds=60.0,
        probe_fn=probe,
        mountinfo_reader=lambda _p: "1 0 0:0 / /data rw - ext4 /dev/sda1 rw\n",
    )
    first = probe_obj.snapshot(["/data"])
    assert first["/data"]["probe_stale"] is True
    # 等待后台探针完成（间隔 60s，期间 snapshot 不会再起探测）
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        snap = probe_obj.snapshot(["/data"])
        if snap["/data"]["reachable"] is True and snap["/data"]["probe_stale"] is False:
            break
        time.sleep(0.05)
    else:
        pytest.fail(f"probe never completed: {probe_obj.snapshot(['/data'])}")
    assert calls == ["/data"]
