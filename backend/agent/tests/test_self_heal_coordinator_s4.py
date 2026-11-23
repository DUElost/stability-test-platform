"""#2972 / ADR-0037 D9：持久化熔断、claim_loop 暂停互斥、xHCI 与 L3 adb offline 自愈测试。"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from types import SimpleNamespace

from backend.agent.active_job_bindings import ActiveJobOccupancy
from backend.agent.claim_loop import process_claim_tick
from backend.agent.device_discovery import parse_lsusb_summary
from backend.agent.self_heal_coordinator import (
    ClaimPauseGate,
    MARKER_PROBE_FAILED,
    SelfHealCoordinator,
)
from backend.agent.xhci_auto_rebind import (
    Action,
    EMPTY_TREE_TICKS_NEED,
    PersistentFuseStore,
    REASON_FUSE_BOOT,
    REASON_FUSE_CORRUPT,
    REASON_FUSE_PROBE_FAILED,
    REASON_FUSE_RETRY,
    REASON_FUSE_WINDOW,
    REASON_WRAPPER_UNAVAILABLE,
    RebindFuse,
    WINDOW_48H_SECONDS,
)


# ── PersistentFuseStore：boot_id 持久化与防洗白 ──────────────────────────────


def test_persistent_fuse_survives_agent_restart_same_boot(tmp_path: Path):
    path = tmp_path / "xhci_rebind_fuse.json"
    store1 = PersistentFuseStore(path, boot_id_provider=lambda: "boot-aaa")
    fuse1 = store1.load(now=1000.0)
    fuse1.record_attempt(now=1000.0)
    assert store1.save(fuse1, now=1000.0) is True
    assert (os.stat(path).st_mode & 0o777) == 0o600

    # 模拟 Agent 热更/重启：同 boot_id 下完整恢复计数
    store2 = PersistentFuseStore(path, boot_id_provider=lambda: "boot-aaa")
    fuse2 = store2.load(now=1010.0)
    assert fuse2.boot_attempts == 1
    assert len(fuse2.window_attempts) == 1
    assert fuse2.open_reason is None

    fuse2.record_attempt(now=1020.0)
    assert fuse2.open_reason == REASON_FUSE_BOOT
    assert store2.save(fuse2, now=1020.0) is True

    store3 = PersistentFuseStore(path, boot_id_provider=lambda: "boot-aaa")
    fuse3 = store3.load(now=1030.0)
    assert fuse3.boot_attempts == 2
    assert fuse3.open_reason == REASON_FUSE_BOOT


def test_persistent_fuse_cross_boot_clears_boot_but_keeps_48h_window(tmp_path: Path):
    path = tmp_path / "xhci_rebind_fuse.json"
    store1 = PersistentFuseStore(path, boot_id_provider=lambda: "boot-1")
    fuse1 = RebindFuse()
    fuse1.record_attempt(now=10_000.0)
    fuse1.record_attempt(now=10_050.0)
    assert fuse1.open_reason == REASON_FUSE_BOOT
    assert store1.save(fuse1, now=10_050.0) is True

    # 跨 boot 但仍在 48h 内：boot_attempts 清零，但 48h 上限仍生效
    store2 = PersistentFuseStore(path, boot_id_provider=lambda: "boot-2")
    fuse2 = store2.load(now=10_500.0)
    assert fuse2.boot_attempts == 0
    assert fuse2.episode_failures == 0
    assert len(fuse2.window_attempts) == 2
    assert fuse2.open_reason == REASON_FUSE_WINDOW

    # 超过 48h 后，window_attempts 裁剪归零，自动解除窗口熔断
    fuse3 = store2.load(now=10_050.0 + WINDOW_48H_SECONDS + 10.0)
    assert fuse3.window_attempts == []
    assert fuse3.open_reason is None


def test_persistent_fuse_corrupt_or_bad_mode_or_symlink_fails_closed(tmp_path: Path):
    # 1. 损坏 JSON
    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{bad json", encoding="utf-8")
    f1 = PersistentFuseStore(corrupt, boot_id_provider=lambda: "b1").load(now=100.0)
    assert f1.open_reason == REASON_FUSE_CORRUPT

    # 2. 组/其他可写权限异常
    bad_mode = tmp_path / "bad_mode.json"
    bad_mode.write_text('{"schema":1,"boot_id":"b1","boot_attempts":0,"window_attempts":[],"episode_failures":0,"open_reason":null}', encoding="utf-8")
    os.chmod(bad_mode, 0o660)
    f2 = PersistentFuseStore(bad_mode, boot_id_provider=lambda: "b1").load(now=100.0)
    assert f2.open_reason == REASON_FUSE_CORRUPT

    # 3. symlink
    real_f = tmp_path / "real.json"
    real_f.write_text('{"schema":1,"boot_id":"b1","boot_attempts":0,"window_attempts":[],"episode_failures":0,"open_reason":null}', encoding="utf-8")
    os.chmod(real_f, 0o600)
    link_f = tmp_path / "link.json"
    link_f.symlink_to(real_f)
    f3 = PersistentFuseStore(link_f, boot_id_provider=lambda: "b1").load(now=100.0)
    assert f3.open_reason == REASON_FUSE_CORRUPT


# ── ClaimPauseGate × process_claim_tick 互斥（含 vbus_slots）──────────────────


def test_claim_pause_gate_blocks_normal_and_vbus_claims(monkeypatch):
    gate = ClaimPauseGate()
    occupancy = ActiveJobOccupancy(
        lock=threading.Lock(),
        job_ids=set(),
        device_ids=set(),
        job_tokens={},
        device_owner={},
        claim_pause_gate=gate,
    )
    fetched = []
    monkeypatch.setattr(
        "backend.agent.claim_loop.fetch_pending_jobs",
        lambda *a, **kw: fetched.append((a, kw)) or [],
    )
    monkeypatch.setattr("backend.agent.claim_loop.vbus_claim_slots", lambda: 2)

    assert gate.acquire_pause(timeout=1.0) is True
    try:
        process_claim_tick(
            api_url="http://server",
            host_id="h1",
            agent_instance_id="inst1",
            occupancy=occupancy,
            heartbeat_thread=SimpleNamespace(effective_slots=3),
            register_active_job=lambda *a, **kw: None,
            deregister_active_job=lambda *a, **kw: None,
            lease_renewer=SimpleNamespace(),
            local_db=SimpleNamespace(get_active_jobs=lambda: []),
            coordinator=SimpleNamespace(),
            executor=SimpleNamespace(),
            adb=SimpleNamespace(),
            job_runner_state=SimpleNamespace(),
            mq_producer=SimpleNamespace(),
            script_registry=SimpleNamespace(),
            patrol_checkpoint_store=SimpleNamespace(),
            operation_scheduler=SimpleNamespace(),
            step_trace_uploader=SimpleNamespace(),
            run_task_wrapper=lambda *a, **kw: None,
        )
        assert fetched == [], "暂停期间即使 effective_slots>0 且 vbus_slots>0 也不得认领"
    finally:
        gate.release_pause()

    # 释放暂停后恢复认领
    process_claim_tick(
        api_url="http://server",
        host_id="h1",
        agent_instance_id="inst1",
        occupancy=occupancy,
        heartbeat_thread=SimpleNamespace(effective_slots=3),
        register_active_job=lambda *a, **kw: None,
        deregister_active_job=lambda *a, **kw: None,
        lease_renewer=SimpleNamespace(),
        local_db=SimpleNamespace(get_active_jobs=lambda: []),
        coordinator=SimpleNamespace(),
        executor=SimpleNamespace(),
        adb=SimpleNamespace(),
        job_runner_state=SimpleNamespace(),
        mq_producer=SimpleNamespace(),
        script_registry=SimpleNamespace(),
        patrol_checkpoint_store=SimpleNamespace(),
        operation_scheduler=SimpleNamespace(),
        step_trace_uploader=SimpleNamespace(),
        run_task_wrapper=lambda *a, **kw: None,
    )
    assert len(fetched) == 1


def test_claim_pause_gate_waits_for_inflight_claim_tick_to_exit():
    """并发锁定：acquire_pause 必须等待在途 claim_scope 退出后才返回 True。"""
    gate = ClaimPauseGate()
    entered = threading.Event()
    release_claim = threading.Event()
    paused_after_claim_exit = threading.Event()
    order: list[str] = []

    def inflight_claim():
        with gate.claim_scope() as allowed:
            assert allowed is True
            order.append("claim_enter")
            entered.set()
            release_claim.wait(timeout=2.0)
            order.append("claim_exit")

    def acquire_worker():
        assert gate.acquire_pause(timeout=2.0) is True
        order.append("pause_acquired")
        paused_after_claim_exit.set()
        gate.release_pause()

    t_claim = threading.Thread(target=inflight_claim)
    t_pause = threading.Thread(target=acquire_worker)
    t_claim.start()
    assert entered.wait(timeout=2.0) is True
    t_pause.start()
    # 在途 tick 未退出前，acquire_pause 不得提前完成
    assert paused_after_claim_exit.wait(timeout=0.1) is False
    release_claim.set()
    t_claim.join(timeout=2.0)
    t_pause.join(timeout=2.0)
    assert order == ["claim_enter", "claim_exit", "pause_acquired"]


# ── SelfHealCoordinator：xHCI rebind 闭环 ────────────────────────────────────


def _make_coord(tmp_path: Path, **overrides) -> SelfHealCoordinator:
    clock = overrides.pop("clock", {"wall": 10_000.0, "mono": 500.0})
    params = dict(
        host_id="172-21-15-88",
        state_dir=tmp_path,
        boot_id_provider=lambda: "boot-xyz",
        wrapper_has_xhci=lambda: True,
        run_xhci_rebind=lambda: (0, None, "STP_XHCI_REBIND_OK count=1"),
        probe_usb_summary=lambda: (8, 2, 0),
        sleep_fn=lambda s: None,
        wall_clock=lambda: clock["wall"],
        mono_clock=lambda: clock["mono"],
        cooldown_seconds=15.0,
        background=False,
    )
    params.update(overrides)
    coord = SelfHealCoordinator(**params)
    coord.update_control_plane_hints(host_ledger_device_rows=8, in_maintenance=False)
    return coord


def test_xhci_rebind_holds_claim_pause_through_cooldown_and_recovers(tmp_path: Path):
    gate = ClaimPauseGate()
    pause_states: list[tuple[str, bool]] = []

    def fake_rebind():
        pause_states.append(("rebind", gate.is_paused))
        return 0, None, "ok"

    def fake_sleep(seconds):
        pause_states.append((f"sleep_{seconds}", gate.is_paused))

    coord = _make_coord(
        tmp_path,
        claim_pause_gate=gate,
        run_xhci_rebind=fake_rebind,
        sleep_fn=fake_sleep,
    )
    for _ in range(EMPTY_TREE_TICKS_NEED - 1):
        act, _ = coord.maybe_trigger_xhci_rebind(
            enabled=True, whitelist=frozenset({"172-21-15-88"}),
            usb_device_count=0, usb_root_hub_count=2, other_usb_nodes=0,
            discovered_devices=0, active_jobs=0, active_devices=0,
            snapshot_monotonic=500.0, poll_interval=5.0,
        )
        assert act == Action.NOOP

    act, reason = coord.maybe_trigger_xhci_rebind(
        enabled=True, whitelist=frozenset({"172-21-15-88"}),
        usb_device_count=0, usb_root_hub_count=2, other_usb_nodes=0,
        discovered_devices=0, active_jobs=0, active_devices=0,
        snapshot_monotonic=500.0, poll_interval=5.0,
    )
    assert act == Action.REBIND and reason is None
    assert pause_states == [("rebind", True), ("sleep_15.0", True)]
    assert gate.is_paused is False

    snap = coord.snapshot()
    assert snap["state"] == "recovered"
    assert snap["fuse_open"] is False
    assert snap["xhci"]["boot_attempts"] == 1
    assert snap["xhci"]["episode_failures"] == 0


def test_xhci_and_l3_persist_attempt_on_disk_before_action_runs(tmp_path: Path):
    """D9.6 防洗白时序钉子：调用 wrapper / adb reconnect 的当下，磁盘状态文件必须已落盘本次 attempt。

    若将 record_attempt/save 挪到动作调用之后，本用例必须变红。
    """
    xhci_fuse_path = tmp_path / "xhci_rebind_fuse.json"
    observed_in_xhci: list[tuple[int, int, int]] = []

    def assert_persisted_during_xhci():
        assert xhci_fuse_path.is_file(), "调用 xhci-rebind 时熔断状态文件必须已落盘"
        mode = os.stat(xhci_fuse_path).st_mode & 0o777
        loaded = PersistentFuseStore(
            xhci_fuse_path, boot_id_provider=lambda: "boot-xyz",
        ).load(now=10_000.0)
        observed_in_xhci.append((loaded.boot_attempts, len(loaded.window_attempts), mode))
        return 0, None, "ok"

    coord = _make_coord(tmp_path, run_xhci_rebind=assert_persisted_during_xhci)
    for _ in range(EMPTY_TREE_TICKS_NEED):
        coord.maybe_trigger_xhci_rebind(
            enabled=True, whitelist=frozenset({"172-21-15-88"}),
            usb_device_count=0, usb_root_hub_count=2, other_usb_nodes=0,
            discovered_devices=0, active_jobs=0, active_devices=0,
            snapshot_monotonic=500.0, poll_interval=5.0,
        )
    assert observed_in_xhci == [(1, 1, 0o600)]

    # 同构验证 L3 adb reconnect offline：调用当下磁盘文件也必须已计入 attempt
    adb_fuse_path = tmp_path / "adb_reconnect_fuse.json"
    observed_in_adb: list[tuple[int, int, int]] = []
    mass_offline = [{"serial": f"S{i}", "adb_state": "offline"} for i in range(4)]

    def assert_persisted_during_adb(_adb: str) -> bool:
        assert adb_fuse_path.is_file(), "调用 adb reconnect 时熔断状态文件必须已落盘"
        mode = os.stat(adb_fuse_path).st_mode & 0o777
        loaded = PersistentFuseStore(
            adb_fuse_path, boot_id_provider=lambda: "boot-xyz",
        ).load(now=10_000.0)
        observed_in_adb.append((loaded.boot_attempts, len(loaded.window_attempts), mode))
        return True

    coord_l3 = _make_coord(
        tmp_path,
        run_adb_reconnect=assert_persisted_during_adb,
        probe_adb_devices=lambda _adb: [{"serial": "S0", "adb_state": "device"}],
    )
    for _ in range(2):
        coord_l3.maybe_trigger_adb_reconnect_offline(
            enabled=True, devices_list=mass_offline, cooldown_seconds=60.0,
            active_jobs=0, active_devices=0, snapshot_monotonic=500.0, poll_interval=5.0,
        )
    assert observed_in_adb == [(1, 1, 0o600)]


def test_xhci_probe_failed_trips_hardware_suspect_immediately(tmp_path: Path):
    coord = _make_coord(
        tmp_path,
        run_xhci_rebind=lambda: (3, MARKER_PROBE_FAILED, "enable_is_0"),
    )
    for _ in range(EMPTY_TREE_TICKS_NEED):
        coord.maybe_trigger_xhci_rebind(
            enabled=True, whitelist=frozenset({"172-21-15-88"}),
            usb_device_count=0, usb_root_hub_count=2, other_usb_nodes=0,
            discovered_devices=0, active_jobs=0, active_devices=0,
            snapshot_monotonic=500.0, poll_interval=5.0,
        )
    snap = coord.snapshot()
    assert snap["state"] == "hardware_suspect"
    assert snap["fuse_open"] is True
    assert snap["fuse_reason"] == REASON_FUSE_PROBE_FAILED
    assert snap["xhci"]["boot_attempts"] == 1

    # 下一拍不再调用 wrapper
    calls = []
    coord._run_xhci_rebind = lambda: calls.append(1) or (0, None, "ok")
    act, reason = coord.maybe_trigger_xhci_rebind(
        enabled=True, whitelist=frozenset({"172-21-15-88"}),
        usb_device_count=0, usb_root_hub_count=2, other_usb_nodes=0,
        discovered_devices=0, active_jobs=0, active_devices=0,
        snapshot_monotonic=500.0, poll_interval=5.0,
    )
    assert act == Action.STOP_HARDWARE_SUSPECT
    assert reason == REASON_FUSE_PROBE_FAILED
    assert calls == []


def test_xhci_recheck_aborts_when_stale_or_local_db_busy(tmp_path: Path):
    calls = []
    clock = {"wall": 10_000.0, "mono": 520.0}
    coord = _make_coord(
        tmp_path,
        clock=clock,
        run_xhci_rebind=lambda: calls.append(1) or (0, None, "ok"),
    )
    # 快照已过时（520 - 500 = 20s > poll_interval 5s）
    for _ in range(EMPTY_TREE_TICKS_NEED):
        coord.maybe_trigger_xhci_rebind(
            enabled=True, whitelist=frozenset({"172-21-15-88"}),
            usb_device_count=0, usb_root_hub_count=2, other_usb_nodes=0,
            discovered_devices=0, active_jobs=0, active_devices=0,
            snapshot_monotonic=500.0, poll_interval=5.0,
        )
    assert calls == []
    assert coord.snapshot()["xhci"]["boot_attempts"] == 0

    # local_db 有活跃任务也必须在持闸复核时放弃
    coord2 = _make_coord(
        tmp_path / "c2",
        get_local_db_active_count=lambda: 1,
        run_xhci_rebind=lambda: calls.append(2) or (0, None, "ok"),
    )
    for _ in range(EMPTY_TREE_TICKS_NEED):
        coord2.maybe_trigger_xhci_rebind(
            enabled=True, whitelist=frozenset({"172-21-15-88"}),
            usb_device_count=0, usb_root_hub_count=2, other_usb_nodes=0,
            discovered_devices=0, active_jobs=0, active_devices=0,
            snapshot_monotonic=500.0, poll_interval=5.0,
        )
    assert calls == []


def test_xhci_wrapper_unavailable_returns_noop(tmp_path: Path):
    coord = _make_coord(tmp_path, wrapper_has_xhci=lambda: False)
    for _ in range(EMPTY_TREE_TICKS_NEED - 1):
        coord.maybe_trigger_xhci_rebind(
            enabled=True, whitelist=frozenset({"172-21-15-88"}),
            usb_device_count=0, usb_root_hub_count=2, other_usb_nodes=0,
            discovered_devices=0, active_jobs=0, active_devices=0,
            snapshot_monotonic=500.0, poll_interval=5.0,
        )
    act, reason = coord.maybe_trigger_xhci_rebind(
        enabled=True, whitelist=frozenset({"172-21-15-88"}),
        usb_device_count=0, usb_root_hub_count=2, other_usb_nodes=0,
        discovered_devices=0, active_jobs=0, active_devices=0,
        snapshot_monotonic=500.0, poll_interval=5.0,
    )
    assert act == Action.NOOP and reason == REASON_WRAPPER_UNAVAILABLE


# ── L3 批量 adb offline 非提权同构自愈 ──────────────────────────────────────


def test_l3_adb_reconnect_isomorphic_idle_gate_and_persistent_fuse(tmp_path: Path):
    clock = {"wall": 20_000.0, "mono": 1000.0}
    gate = ClaimPauseGate()
    reconnect_calls: list[tuple[str, bool]] = []
    mass_offline = [{"serial": f"S{i}", "adb_state": "offline"} for i in range(6)]

    coord = _make_coord(
        tmp_path,
        clock=clock,
        claim_pause_gate=gate,
        run_adb_reconnect=lambda adb: reconnect_calls.append((adb, gate.is_paused)) or True,
        probe_adb_devices=lambda adb: mass_offline,  # 模拟两次都未恢复
    )

    # 非空闲窗必须阻断
    coord.maybe_trigger_adb_reconnect_offline(
        enabled=True, devices_list=mass_offline, cooldown_seconds=60.0,
        active_jobs=1, active_devices=0, snapshot_monotonic=1000.0, poll_interval=5.0,
    )
    act, reason = coord.maybe_trigger_adb_reconnect_offline(
        enabled=True, devices_list=mass_offline, cooldown_seconds=60.0,
        active_jobs=1, active_devices=0, snapshot_monotonic=1000.0, poll_interval=5.0,
    )
    assert act == Action.NOOP and reason == "active_jobs"
    assert reconnect_calls == []

    # 空闲窗触发第 1 次失败
    coord.maybe_trigger_adb_reconnect_offline(
        enabled=True, devices_list=mass_offline, cooldown_seconds=60.0,
        active_jobs=0, active_devices=0, snapshot_monotonic=1000.0, poll_interval=5.0,
    )
    assert len(reconnect_calls) == 1
    assert reconnect_calls[0][1] is True  # 执行期间持有暂停门闸
    assert gate.is_paused is False

    # 冷却过期后第 2 次仍未恢复 → 熔断（达到同 boot 上限 / 重试耗尽）
    clock["mono"] += 120.0
    clock["wall"] += 120.0
    coord.maybe_trigger_adb_reconnect_offline(
        enabled=True, devices_list=mass_offline, cooldown_seconds=60.0,
        active_jobs=0, active_devices=0, snapshot_monotonic=clock["mono"], poll_interval=5.0,
    )
    coord.maybe_trigger_adb_reconnect_offline(
        enabled=True, devices_list=mass_offline, cooldown_seconds=60.0,
        active_jobs=0, active_devices=0, snapshot_monotonic=clock["mono"], poll_interval=5.0,
    )
    assert len(reconnect_calls) == 2
    snap = coord.snapshot()
    assert snap["fuse_open"] is True
    assert snap["adb_reconnect"]["fuse_open"] is True
    assert snap["adb_reconnect"]["fuse_reason"] in (REASON_FUSE_RETRY, REASON_FUSE_BOOT)


def test_parse_lsusb_summary_separates_targets_root_hubs_and_other_nodes():
    sample = "\n".join([
        "Bus 002 Device 001: ID 1d6b:0003 Linux Foundation 3.0 root hub",
        "Bus 001 Device 001: ID 1d6b:0002 Linux Foundation 2.0 root hub",
        "Bus 001 Device 002: ID 0bda:5411 Realtek Semiconductor Corp. RTS5411 Hub",
        "Bus 001 Device 003: ID 046d:c52b Logitech, Inc. Unifying Receiver",
        "Bus 001 Device 013: ID 0e8d:2046 MediaTek Inc. MLD-LX2",
    ])
    assert parse_lsusb_summary(sample) == (1, 2, 2)
