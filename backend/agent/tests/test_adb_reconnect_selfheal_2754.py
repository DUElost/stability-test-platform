# -*- coding: utf-8 -*-
"""#2754 自愈半边：批量 adb offline 的 ``reconnect offline`` 自动巡检。

原缺口（host .81 实证）：15/16 台 adb offline、`lsusb` 枚举层完好——
``adb reconnect offline`` 一条命令即可恢复，但无人巡检就永远停在 offline，
host 状态 ONLINE 完全看不出。本切片给 Agent 加**默认关**的自动巡检能力。

锁定的不变量：
1. 门控：``STP_ADB_RECONNECT_AUTO != "1"`` 或 settings 缺失（#2279 降级）→ 永不触发；
2. 阈值：offline ≥ 2 且 ≥ 半数才叫「批量」——单台 offline / 少数派不触发；
3. 持久性：连续 2 拍满足才动手（躲瞬时抖动），中间恢复健康即清零计数；
4. 冷却：触发后 ``STP_ADB_RECONNECT_COOLDOWN_SECONDS`` 内不重复；
5. 命令形状：``adb reconnect offline``（host 级、只作用 offline 设备）。
"""

from __future__ import annotations

from types import SimpleNamespace

from backend.agent import device_discovery
from backend.agent.heartbeat_thread import HeartbeatThread


def _settings(enabled: bool = True, cooldown: float = 600.0):
    return SimpleNamespace(
        adb_reconnect_auto_enabled=enabled,
        stp_adb_reconnect_cooldown_seconds=cooldown,
    )


def _devices(total: int, offline: int):
    devs = [
        {"serial": f"S{i}", "adb_state": "offline"} for i in range(offline)
    ]
    devs += [{"serial": f"S{i}", "adb_state": "device"} for i in range(offline, total)]
    return devs


def _make_thread():
    return HeartbeatThread(
        api_url="http://server", host_id="h", adb_path="adb",
        mount_points=[], host_info={}, poll_interval=60,
    )


def test_gate_off_never_triggers(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        "backend.agent.heartbeat_thread.device_discovery.adb_reconnect_offline",
        lambda adb: calls.append(adb),
    )
    th = _make_thread()
    mass = _devices(16, 15)

    th._maybe_auto_reconnect_offline(mass, _settings(enabled=False))
    th._maybe_auto_reconnect_offline(mass, None)  # #2279 降级
    assert calls == [], "默认关 / settings 缺失都不得触发"
    # 门控关闭时连持久计数都不应推进
    assert th._mass_offline_ticks == 0


def test_threshold_edges(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        "backend.agent.heartbeat_thread.device_discovery.adb_reconnect_offline",
        lambda adb: calls.append(adb),
    )
    th = _make_thread()

    # 单台 offline（2 台里的 1 台）——不叫批量
    th._maybe_auto_reconnect_offline(_devices(2, 1), _settings())
    # 少数派：10 台里 2 台 offline（20%）——不叫批量
    th._maybe_auto_reconnect_offline(_devices(10, 2), _settings())
    assert calls == []
    assert th._mass_offline_ticks == 0, "不满足阈值的拍不得累计持久计数"

    # 批量：3 台里 2 台 offline（67%）——第一拍只登记
    th._maybe_auto_reconnect_offline(_devices(3, 2), _settings())
    assert calls == []
    assert th._mass_offline_ticks == 1


def test_persistence_two_ticks_and_recovery_reset(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        "backend.agent.heartbeat_thread.device_discovery.adb_reconnect_offline",
        lambda adb: calls.append(adb),
    )
    th = _make_thread()

    th._maybe_auto_reconnect_offline(_devices(16, 15), _settings())
    assert calls == []  # 第一拍：只登记
    th._maybe_auto_reconnect_offline(_devices(16, 15), _settings())
    assert len(calls) == 1  # 第二拍：触发
    assert th._mass_offline_ticks == 0  # 触发后计数清零

    # 中途恢复健康 → 计数清零，重新累计也要再等两拍
    th._maybe_auto_reconnect_offline(_devices(16, 15), _settings())
    th._maybe_auto_reconnect_offline(_devices(16, 0), _settings())
    assert th._mass_offline_ticks == 0
    th._maybe_auto_reconnect_offline(_devices(16, 15), _settings())
    assert len(calls) == 1, "健康后持久性必须重新起算"


def test_cooldown_suppresses_then_expires(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        "backend.agent.heartbeat_thread.device_discovery.adb_reconnect_offline",
        lambda adb: calls.append(adb),
    )
    clock = {"now": 1000.0}
    monkeypatch.setattr(
        "backend.agent.heartbeat_thread.time",
        SimpleNamespace(monotonic=lambda: clock["now"]),
    )
    th = _make_thread()
    th._last_reconnect_offline_at = clock["now"]  # 刚触发过

    th._maybe_auto_reconnect_offline(_devices(16, 15), _settings())
    th._maybe_auto_reconnect_offline(_devices(16, 15), _settings())
    assert calls == [], "冷却期内即使连续两拍批量 offline 也不得触发"

    clock["now"] += 601  # 跨过 600s 冷却
    th._maybe_auto_reconnect_offline(_devices(16, 15), _settings())
    th._maybe_auto_reconnect_offline(_devices(16, 15), _settings())
    assert len(calls) == 1, "冷却过期后应可再次触发"


def test_reconnect_command_shape(monkeypatch):
    """``adb reconnect offline`` 的命令形状与失败语义。"""
    seen: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        seen.append(cmd)
        assert kwargs.get("timeout") is not None
        return SimpleNamespace(returncode=0, stdout="reconnecting offline devices\n", stderr="")

    monkeypatch.setattr(device_discovery.subprocess, "run", fake_run)
    assert device_discovery.adb_reconnect_offline("adb") is True
    assert seen[0] == ["adb", "reconnect", "offline"]

    monkeypatch.setattr(
        device_discovery.subprocess, "run",
        lambda cmd, **kw: SimpleNamespace(returncode=1, stdout="", stderr="fail"),
    )
    assert device_discovery.adb_reconnect_offline("adb") is False

    def raising_run(cmd, **kwargs):
        raise FileNotFoundError("no adb")

    monkeypatch.setattr(device_discovery.subprocess, "run", raising_run)
    assert device_discovery.adb_reconnect_offline("adb") is False
