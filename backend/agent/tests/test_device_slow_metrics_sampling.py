# -*- coding: utf-8 -*-
"""设备慢指标（电量/温度/版本/网络延迟）低频采样：per-serial due 门控 + 缓存回填。

背景：在线状态（adb devices + echo 连通性）是关键信号，但慢探测（dumpsys 10s、
ping 最坏 15s×2）此前**每拍全量**执行，把承载在线状态的心跳 tick 拖长——降频慢
指标即是提升在线状态响应。#2757 的 disk 已确立「低频采样 + 缓存每拍带上」形态，
本单把同一形态推广到其余慢指标，并因用户裁决的三个场景把门控做成**按 serial**：

1. 新设备接入（无缓存）→ 首拍立即采，不空一个窗口；
2. 断线后自动恢复（上一拍非连通）→ 回线首拍强制重采，不显示断电前旧值；
3. 开关机专项（设备反复重启）→ 每次回线都是场景 2，天然逐周期新鲜；
回退面：窗口 <=0 或 settings 不可用（#2279）→ 不节流 = 每拍采集（改造前行为）。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.agent import device_discovery
from backend.agent.heartbeat_thread import HeartbeatThread


# ── device_discovery 层：include_metrics 短路慢探测 ──────────────────────

def test_collect_device_info_skips_slow_probes_when_metrics_disabled(monkeypatch):
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout="test", stderr="")

    def no_ping(*a, **k):
        raise AssertionError("ping must not run when include_metrics=False")

    monkeypatch.setattr(device_discovery.subprocess, "run", fake_run)
    monkeypatch.setattr(device_discovery, "_ping_with_fallback", no_ping)
    monkeypatch.setattr(device_discovery, "detect_device_platform", lambda adb, serial: "MTK")

    info = device_discovery.collect_device_info("adb", "REAL-1", include_metrics=False)

    # 只跑 echo 连接性快探（platform 走内部缓存桩，不占 adb）
    assert calls == [["adb", "-s", "REAL-1", "shell", "echo", "test"]]
    assert info["adb_state"] == "device"
    assert info["adb_connected"] is True
    assert info["platform"] == "MTK"
    assert info["battery_level"] is None
    assert info["temperature"] is None
    assert info["network_latency"] is None
    assert info["build_display_id"] is None


def test_collect_device_info_default_still_probes_slow_metrics(monkeypatch):
    """默认 include_metrics=True：行为与改造前一致（成功用例的回归哨兵）。"""
    outputs = {
        "echo": SimpleNamespace(returncode=0, stdout="test", stderr=""),
        "dumpsys": SimpleNamespace(returncode=0, stdout="level: 87\ntemperature: 350\n", stderr=""),
        "getprop": SimpleNamespace(returncode=0, stdout="BUILD-X\n", stderr=""),
    }

    def fake_run(cmd, **kwargs):
        joined = " ".join(cmd)
        key = "echo" if "echo" in joined else "dumpsys" if "dumpsys" in joined else "getprop"
        return outputs[key]

    monkeypatch.setattr(device_discovery.subprocess, "run", fake_run)
    monkeypatch.setattr(device_discovery, "_ping_with_fallback", lambda *a, **k: 12.3)
    monkeypatch.setattr(device_discovery, "detect_device_platform", lambda adb, serial: "MTK")

    stages = []
    info = device_discovery.collect_device_info(
        "adb", "REAL-1", timing_sink=lambda stage, seconds: stages.append((stage, seconds)),
    )
    assert info["battery_level"] == 87
    assert info["temperature"] == 35
    assert info["network_latency"] == 12.3
    assert info["build_display_id"] == "BUILD-X"
    assert [stage for stage, _ in stages] == ["fast", "slow"]
    assert all(seconds >= 0 for _, seconds in stages)


# ── HeartbeatThread 层：due 判定矩阵 ─────────────────────────────────────

def _bare_thread(**slow_interval) -> HeartbeatThread:
    thread = HeartbeatThread(
        api_url="http://server", host_id="h", adb_path="adb",
        mount_points=[], host_info={}, poll_interval=60,
    )
    for k, v in slow_interval.items():
        setattr(thread, k, v)
    return thread


def test_slow_metrics_due_matrix():
    thread = _bare_thread(_slow_sample_interval=1800.0)

    # 新设备首见（无缓存）→ 强采
    assert thread._slow_metrics_due("NEW", "device") is True

    # 已有缓存 + 上拍连通 + 窗口未到 → 不采
    thread._slow_metrics_by_serial["S1"] = {"battery_level": 80, "temperature": 30,
                                            "network_latency": 5.0, "build_display_id": "B"}
    thread._slow_metrics_next_due["S1"] = 1e12  # 窗口远未到期
    thread._last_adb_connected_by_serial["S1"] = True
    assert thread._slow_metrics_due("S1", "device") is False

    # 窗口到期 → 采
    thread._slow_metrics_next_due["S1"] = 0.0
    assert thread._slow_metrics_due("S1", "device") is True

    # 断线恢复（上拍非连通）→ 即使窗口未到也强采
    thread._slow_metrics_next_due["S1"] = 1e12
    thread._last_adb_connected_by_serial["S1"] = False
    assert thread._slow_metrics_due("S1", "device") is True

    # 缓存已有 + 连通 + 窗口未到，但 raw 非 device → 不必采（collect 侧也短路）
    thread._last_adb_connected_by_serial["S1"] = True
    assert thread._slow_metrics_due("S1", "offline") is False

    # 窗口<=0：不节流，恒 due（回退改造前行为）
    thread._slow_sample_interval = 0.0
    assert thread._slow_metrics_due("S1", "device") is True


def test_absorb_keeps_old_values_on_partial_failure():
    """探测失败的 None 键不覆盖上一轮好值（disk #2757 同形的保守合并）。"""
    thread = _bare_thread(_slow_sample_interval=1800.0)
    good = {"battery_level": 80, "temperature": 30, "network_latency": 5.0,
            "build_display_id": "B1"}
    thread._absorb_slow_metrics("S1", {**good, "adb_connected": True}, collected=True)
    assert thread._slow_metrics_by_serial["S1"] == good

    # 第二轮 ping 挂了（latency None），电量温度有新值 → None 键保留旧值
    fresh = {"battery_level": 79, "temperature": 31, "network_latency": None,
             "build_display_id": "B1", "adb_connected": True}
    thread._absorb_slow_metrics("S1", fresh, collected=True)
    assert thread._slow_metrics_by_serial["S1"] == {
        "battery_level": 79, "temperature": 31, "network_latency": 5.0,
        "build_display_id": "B1",
    }
    # info 被回填为合并结果（payload=缓存 单一口径）
    assert fresh["network_latency"] == 5.0


def test_absorb_all_none_does_not_create_cache():
    """全失败（echo 过但三项探测都拿不到，如重启后服务未稳）：不建缓存条目，
    下一拍仍按「首见强采」重试，而不是锁进 30 分钟窗口。"""
    thread = _bare_thread(_slow_sample_interval=1800.0)
    info = {"adb_connected": True, "battery_level": None, "temperature": None,
            "network_latency": None, "build_display_id": None}
    thread._absorb_slow_metrics("S1", info, collected=True)
    assert "S1" not in thread._slow_metrics_by_serial
    assert "S1" not in thread._slow_metrics_next_due


# ── _tick 端到端：缓存带上 / 窗口节流 / 断线恢复强采 / 回退 ────────────────

def _tick_harness(monkeypatch, interval: float):
    """返回 (thread, sent, probe_log, set_online)，驱动 _tick 并记录慢采样节奏。"""
    state = {
        "online": True,
        "raw": "device",
        "seq": 0,
        "metrics_calls": [],
        "now": [1000.0],
    }

    monkeypatch.setattr(
        "backend.agent.heartbeat_thread.time",
        SimpleNamespace(monotonic=lambda: state["now"][0]),
    )

    def fake_discover(adb):
        return [{"serial": "S1", "adb_state": state["raw"], "model": None}]

    def fake_collect(adb_path, serial, raw_adb_state="device", include_metrics=True, timing_sink=None):
        state["metrics_calls"].append(include_metrics)
        if raw_adb_state != "device" or not state["online"]:
            return {"adb_state": "offline", "adb_connected": False}
        info: dict = {"adb_state": "device", "adb_connected": True, "platform": "MTK"}
        if include_metrics:
            state["seq"] += 1
            info.update(
                battery_level=state["seq"], temperature=30,
                network_latency=5.0, build_display_id="B%d" % state["seq"],
            )
        return info

    sent = []

    def fake_send(*a, **k):
        sent.append(k)
        return {"ok": True}

    monkeypatch.setattr(
        "backend.agent.heartbeat_thread.device_discovery.discover_devices", fake_discover,
    )
    monkeypatch.setattr(
        "backend.agent.heartbeat_thread.device_discovery.collect_device_info", fake_collect,
    )
    monkeypatch.setattr("backend.agent.heartbeat_thread.send_heartbeat", fake_send)

    thread = HeartbeatThread(
        api_url="http://server", host_id="h", adb_path="adb",
        mount_points=[], host_info={}, poll_interval=60,
    )
    settings = SimpleNamespace(
        stp_device_info_sample_interval_seconds=interval,
        stp_device_disk_sample_interval_seconds=0,   # disk 关闭，隔离被测面
        adb_reconnect_auto_enabled=False,            # #2754 巡检不参与
    )
    monkeypatch.setattr(thread, "_read_heartbeat_settings_safe", lambda: settings)
    return thread, sent, state


def _batteries(sent):
    return [d["battery_level"] for s in sent for d in s["devices"]]


def test_tick_throttles_within_window_and_forces_on_reconnect(monkeypatch):
    thread, sent, state = _tick_harness(monkeypatch, interval=1800.0)

    # 拍1：新设备首见 → 强采
    thread._tick()
    state["now"][0] += 10
    # 拍2：窗口内 → 只做 echo，payload 带缓存值（不空窗）
    thread._tick()
    assert [d["battery_level"] for d in sent[-1]["devices"]] == [1]

    # 拍3：断线（连通性判 False）→ payload 慢指标为 None（与改造前 offline 同形，
    # 控制面 _update_if_not_none 保留库中旧值）
    state["online"] = False
    state["now"][0] += 10
    thread._tick()
    assert [d["battery_level"] for d in sent[-1]["devices"]] == [None]

    # 拍4：回线 → 上拍非连通 → 窗口未到也强制重采（断电期间电量温度已变，旧缓存会误导）
    state["online"] = True
    state["now"][0] += 10
    thread._tick()
    assert [d["battery_level"] for d in sent[-1]["devices"]] == [2]

    # 拍5：稳定在线且窗口未到 → 回到节流
    state["now"][0] += 10
    thread._tick()

    # include_metrics 节奏：首采 T，窗口内 F，断线拍 F（非 due 且 raw 非 device），
    # 回线 T（强制），稳定 F
    assert state["metrics_calls"] == [True, False, False, True, False]

    # 拍6：跨过 1800s 窗口 → 到期重采
    state["now"][0] += 1791
    thread._tick()
    assert state["metrics_calls"][-1] is True
    assert [d["battery_level"] for d in sent[-1]["devices"]] == [3]


def test_tick_interval_zero_falls_back_to_every_tick(monkeypatch):
    """0/负 = 不节流：每拍都带慢指标探测（回退改造前行为的安全档）。"""
    thread, sent, state = _tick_harness(monkeypatch, interval=0.0)
    thread._tick()
    state["now"][0] += 1
    thread._tick()
    state["now"][0] += 1
    thread._tick()
    assert state["metrics_calls"] == [True, True, True]
    assert [d["battery_level"] for d in sent[-1]["devices"]] == [3]


def test_tick_settings_unavailable_collects_every_tick(monkeypatch):
    """#2279：settings 读不到 → 不节流（宁可不降频，不可丢新鲜度）。"""
    thread, sent, state = _tick_harness(monkeypatch, interval=1800.0)
    thread._tick()  # 建立缓存

    monkeypatch.setattr(thread, "_read_heartbeat_settings_safe", lambda: None)
    state["now"][0] += 1
    thread._tick()
    assert state["metrics_calls"][-1] is True


# ── Settings 层：默认值与语义 ─────────────────────────────────────────────

def test_heartbeat_settings_knob(monkeypatch):
    from backend.agent.settings import HeartbeatSettings

    monkeypatch.delenv("STP_DEVICE_INFO_SAMPLE_INTERVAL_SECONDS", raising=False)
    assert HeartbeatSettings().stp_device_info_sample_interval_seconds == 1800

    monkeypatch.setenv("STP_DEVICE_INFO_SAMPLE_INTERVAL_SECONDS", "0")
    # 0 是合法档位（不节流），不得被钳制成正数
    assert HeartbeatSettings().stp_device_info_sample_interval_seconds == 0

    monkeypatch.setenv("STP_DEVICE_INFO_SAMPLE_INTERVAL_SECONDS", "60")
    assert HeartbeatSettings().stp_device_info_sample_interval_seconds == 60


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
