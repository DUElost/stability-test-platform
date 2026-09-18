# -*- coding: utf-8 -*-
"""#2757：设备 disk_total/disk_used 心跳覆盖率 0 → 低频采样 + 上报链接通。

三层各自的锁：
1. `parse_df_data`：toybox 两行 / busybox 两行（1K-blocks）/ toybox 单行三形态
   可解析，垃圾输出保守返回 (None, None)——不猜容量；
2. `collect_device_disk`：静态设备（dev 夹具）不伪造容量；adb 失败 → None 字段；
3. `_maybe_sample_disk`：时间门控（窗口内不重采、间隔到才采）、0/负=关闭、
   settings None（#2279 降级）＝不采样、只采 adb_state=device 的设备；
4. `_tick` 端到端：缓存值随 payload 上送（控制面 `heartbeat.py` 的
   `_update_if_not_none` 已就绪，本单只补 agent 侧链路）。
"""

from __future__ import annotations

from types import SimpleNamespace

from backend.agent import device_discovery
from backend.agent.heartbeat_thread import HeartbeatThread

_G = 1024 ** 3
_K = 1024


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
    monkeypatch.setattr(
        "backend.agent.heartbeat_thread.device_discovery.collect_device_info",
        lambda adb_path, serial, raw_adb_state="device": {
            "adb_state": raw_adb_state,
            "adb_connected": raw_adb_state == "device",
        },
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


def test_parse_df_data_forms():
    """三形态可解析；数字口径＝字节。"""
    toybox_two_line = (
        "Filesystem  Size  Used Avail Use% Mounted on\n"
        "/dev/block/dm-10   110G   58G   52G  53% /data\n"
    )
    assert device_discovery.parse_df_data(toybox_two_line) == (110 * _G, 58 * _G)

    busybox_two_line = (
        "Filesystem   1K-blocks      Used Available Use% Mounted on\n"
        "/dev/block/sda19  115343360  60800000  54543360  53% /data\n"
    )
    assert device_discovery.parse_df_data(busybox_two_line) == (
        115343360 * _K, 60800000 * _K,
    )

    toybox_one_line = "/data: 58G 52G 53% /data"
    assert device_discovery.parse_df_data(toybox_one_line) == (110 * _G, 58 * _G)

    decimal_suffix = "/data: 1.5G 0.5G 75% /data"
    assert device_discovery.parse_df_data(decimal_suffix) == (2 * _G, int(1.5 * _G))


def test_parse_df_data_garbage_is_none_not_guesswork():
    """解析不出必须 (None, None)——覆盖率统计宁可空，不可假。"""
    assert device_discovery.parse_df_data("") == (None, None)
    # 只有表头、没有数据行
    assert device_discovery.parse_df_data(
        "Filesystem  Size  Used Avail Use% Mounted on\n"
    ) == (None, None)
    # 错误信息里恰好含 "/data"
    assert device_discovery.parse_df_data("df: /data: No such file or directory") == (None, None)
    # 数据行存在但容量 token 非数字
    assert device_discovery.parse_df_data("fs  x  y  z  1%  /data") == (None, None)


def test_collect_device_disk_static_and_failure(monkeypatch):
    """静态设备不伪造容量；adb 异常/非零 rc/解析不出 → None 字段。"""
    monkeypatch.setenv("STP_STATIC_DEVICE_SERIALS", "FAKE-1")

    def raising_run(cmd, **kwargs):
        raise FileNotFoundError("no adb in test env")

    monkeypatch.setattr(device_discovery.subprocess, "run", raising_run)
    assert device_discovery.collect_device_disk("adb", "FAKE-1") == {
        "disk_total": None, "disk_used": None,
    }
    assert device_discovery.collect_device_disk("adb", "REAL-1") == {
        "disk_total": None, "disk_used": None,
    }  # adb 失败 → None 字段，不抛

    def fake_run(cmd, **kwargs):
        assert cmd[1:] == ["-s", "REAL-2", "shell", "df", "/data"]
        return SimpleNamespace(returncode=0, stdout="/data: 4G 2G 50% /data", stderr="")

    monkeypatch.setattr(device_discovery.subprocess, "run", fake_run)
    assert device_discovery.collect_device_disk("adb", "REAL-2") == {
        "disk_total": 6 * _G, "disk_used": 4 * _G,
    }


def test_maybe_sample_disk_gate(monkeypatch):
    """时间门控：首拍采样 → 窗口内不重采 → 间隔到重采；0=关；settings None=不采。"""
    calls: list[str] = []
    monkeypatch.setattr(
        "backend.agent.heartbeat_thread.device_discovery.collect_device_disk",
        lambda adb, serial: calls.append(serial) or {"disk_total": 1, "disk_used": 1},
    )
    clock = {"now": 1000.0}
    # 替换 heartbeat_thread 的 time 引用（不动全局 time 模块）
    monkeypatch.setattr(
        "backend.agent.heartbeat_thread.time",
        SimpleNamespace(monotonic=lambda: clock["now"]),
    )
    thread = HeartbeatThread(
        api_url="http://server", host_id="h", adb_path="adb",
        mount_points=[], host_info={}, poll_interval=60,
    )
    discovered = [
        {"serial": "S1", "adb_state": "device"},
        {"serial": "S2", "adb_state": "offline"},  # 非 device 态不采
    ]
    settings = SimpleNamespace(stp_device_disk_sample_interval_seconds=300)

    thread._maybe_sample_disk(discovered, settings)
    assert calls == ["S1"]
    assert thread._disk_by_serial == {"S1": {"disk_total": 1, "disk_used": 1}}

    clock["now"] += 10  # 窗口内
    thread._maybe_sample_disk(discovered, settings)
    assert calls == ["S1"], "窗口内不得重复采样"

    clock["now"] += 291  # 跨过 300s 间隔
    thread._maybe_sample_disk(discovered, settings)
    assert calls == ["S1", "S1"]

    thread._maybe_sample_disk(discovered, None)  # #2279 降级：settings None
    thread._maybe_sample_disk(
        discovered, SimpleNamespace(stp_device_disk_sample_interval_seconds=0),
    )
    assert calls == ["S1", "S1"], "settings 缺失 / 0=关闭 都不得采样"


def test_tick_carries_disk_fields_in_payload(monkeypatch):
    """端到端：采样值进入每拍心跳 payload；未采样设备（offline）字段为 None。"""
    monkeypatch.setattr(
        "backend.agent.heartbeat_thread.device_discovery.collect_device_disk",
        lambda adb, serial: {"disk_total": 118111600640, "disk_used": 62285414400},
    )
    devices = [
        {"serial": "S1", "adb_state": "device", "model": None},
        {"serial": "S2", "adb_state": "offline", "model": None},
    ]
    thread, sent = _make_thread(monkeypatch, devices)
    thread._tick()

    payload_devices = {d["serial"]: d for d in sent[0]["devices"]}
    assert payload_devices["S1"]["disk_total"] == 118111600640
    assert payload_devices["S1"]["disk_used"] == 62285414400
    # offline 设备不采样——None 上送（控制面 _update_if_not_none 跳过，不误清）
    assert payload_devices["S2"]["disk_total"] is None
    assert payload_devices["S2"]["disk_used"] is None
