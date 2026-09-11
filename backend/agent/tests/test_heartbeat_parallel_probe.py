# -*- coding: utf-8 -*-
"""#730：心跳设备探测并发化——单设备 ADB 假死不线性拖垮主循环。

覆盖：
1. 多设备（含 2 台慢探测）时最大并发 ≥ 2，且整轮 `_tick` 耗时远小于串行累加；
2. 结果顺序与 discover 顺序一致（心跳 payload 稳定）；
3. 单设备采集抛异常只影响该设备，不中断整轮心跳。
"""

from __future__ import annotations

import threading
import time

from backend.agent.heartbeat_thread import HeartbeatThread


def _make_thread(monkeypatch, discovered_devices):
    sent_payloads = []

    def fake_send_heartbeat(*args, **kwargs):
        sent_payloads.append(kwargs)
        return {"ok": True}

    monkeypatch.setattr("backend.agent.heartbeat_thread.send_heartbeat", fake_send_heartbeat)
    monkeypatch.setattr(
        "backend.agent.heartbeat_thread.device_discovery.discover_devices",
        lambda adb: discovered_devices,
    )
    thread = HeartbeatThread(
        api_url="http://server",
        host_id="host-1",
        adb_path="adb",
        mount_points=[],
        host_info={},
        poll_interval=60,
    )
    return thread, sent_payloads


def test_tick_probes_devices_concurrently_not_serially(monkeypatch):
    devices = [
        {"serial": "S%d" % i, "adb_state": "device", "model": None}
        for i in range(10)
    ]
    slow = {"S1", "S6"}  # 两台慢探测，均落在首个并发 wave

    inflight = 0
    max_inflight = 0
    lock = threading.Lock()

    def fake_collect(adb_path, serial, raw_adb_state="device"):
        nonlocal inflight, max_inflight
        with lock:
            inflight += 1
            if inflight > max_inflight:
                max_inflight = inflight
        try:
            if serial in slow:
                time.sleep(0.4)
            return {"adb_state": "device", "adb_connected": True}
        finally:
            with lock:
                inflight -= 1

    monkeypatch.setattr(
        "backend.agent.heartbeat_thread.device_discovery.collect_device_info",
        fake_collect,
    )
    thread, _ = _make_thread(monkeypatch, devices)

    started = time.monotonic()
    thread._tick()
    elapsed = time.monotonic() - started

    # 并发证据（串行实现恒为 1）
    assert max_inflight >= 2, "device probes were serialized"
    # 串行：2×0.4=0.8s 起；并发：≈0.4s。余量放宽抵抗 CI 抖动
    assert elapsed < 0.7, "tick elapsed %.3fs suggests serial probing" % elapsed
    # 顺序稳定（与 discover 顺序一致）
    serials = [d["serial"] for d in thread.latest_devices]
    assert serials == [d["serial"] for d in devices]


def test_tick_device_probe_exception_is_isolated(monkeypatch):
    devices = [
        {"serial": "OK-1", "adb_state": "device", "model": None},
        {"serial": "BOOM", "adb_state": "device", "model": None},
    ]

    def fake_collect(adb_path, serial, raw_adb_state="device"):
        if serial == "BOOM":
            raise RuntimeError("unexpected probe failure")
        return {"adb_state": "device", "adb_connected": True}

    monkeypatch.setattr(
        "backend.agent.heartbeat_thread.device_discovery.collect_device_info",
        fake_collect,
    )
    thread, _ = _make_thread(monkeypatch, devices)

    thread._tick()  # 不得抛出

    by_serial = {d["serial"]: d for d in thread.latest_devices}
    assert by_serial["OK-1"]["adb_connected"] is True
    assert by_serial["BOOM"]["adb_connected"] is False
    assert by_serial["BOOM"]["adb_state"] == "error"
