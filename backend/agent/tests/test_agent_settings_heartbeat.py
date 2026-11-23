"""心跳/协调/注册域 Settings 测试（ADR-0042 P2 #3）。

覆盖：默认值逐一对照迁移前 · **严格组的失败面**（非法 → ValidationError，等价于迁移前的
启动 ValueError）· 两个字符串旋钮的**精确派生语义**（仅 `"1"` / 仅 `"true"`，不 strip、
不扩大小写）· min>max 只告警不改值 · env 覆盖 + 缓存语义 · `.env` 负向 ·
消费方（HeartbeatThread / HostRunCoordinator）与 Settings 同源。
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.agent.settings import (
    HeartbeatSettings,
    RegistrationSettings,
    get_heartbeat_settings,
    get_registration_settings,
    reset_agent_settings_caches,
)

_HEARTBEAT_DEFAULTS = {
    "coordinator_heartbeat_interval": 30.0,
    "coordinator_max_plan_run_hosts": 200,
    "stp_heartbeat_interval_min": 10.0,
    "stp_heartbeat_interval_max": 120.0,
    "stp_adb_auto_repair": "0",
    "stp_adb_repair_cooldown_seconds": 300.0,
}

_REGISTRATION_DEFAULTS = {
    "auto_register_host": "false",
    "auto_register_max_retries": 0,
    "auto_register_retry_delay": 10.0,
}

_STRICT_NUMERIC = (
    ("COORDINATOR_HEARTBEAT_INTERVAL", "half-minute"),
    ("COORDINATOR_MAX_PLAN_RUN_HOSTS", "lots"),
    ("STP_HEARTBEAT_INTERVAL_MIN", ""),  # 空串在迁移前也走 float("") → ValueError
    ("STP_HEARTBEAT_INTERVAL_MAX", "2m"),
    ("STP_ADB_REPAIR_COOLDOWN_SECONDS", "5min"),
    ("AUTO_REGISTER_MAX_RETRIES", "many"),
    ("AUTO_REGISTER_RETRY_DELAY", "soon"),
)


@pytest.fixture(autouse=True)
def _clean():
    reset_agent_settings_caches()
    yield
    reset_agent_settings_caches()


def test_defaults_match_pre_migration(monkeypatch):
    for name in _HEARTBEAT_DEFAULTS | _REGISTRATION_DEFAULTS:
        monkeypatch.delenv(name.upper(), raising=False)
    heartbeat = HeartbeatSettings()
    registration = RegistrationSettings()
    for name, expected in _HEARTBEAT_DEFAULTS.items():
        actual = getattr(heartbeat, name)
        assert actual == expected, name
        assert type(actual) is type(expected), f"{name} 类型漂移: {type(actual)}"
    for name, expected in _REGISTRATION_DEFAULTS.items():
        actual = getattr(registration, name)
        assert actual == expected, name
        assert type(actual) is type(expected), f"{name} 类型漂移: {type(actual)}"


@pytest.mark.parametrize("env_name,raw", _STRICT_NUMERIC)
def test_strict_group_raises_like_before(monkeypatch, env_name, raw):
    """全部数值旋钮迁移前都是 `float(...)`/`int(...)` 直转——非法值启动即失败。"""
    monkeypatch.setenv(env_name, raw)
    reset_agent_settings_caches()
    with pytest.raises(ValidationError):
        if env_name.startswith("AUTO_REGISTER_"):
            get_registration_settings()
        else:
            get_heartbeat_settings()


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1", True),
        ("0", False),
        # 迁移前是精确 `== "1"`：以下都不算启用（不 strip、不认 true/yes）
        ("true", False),
        ("TRUE", False),
        ("01", False),
        (" 1", False),
        ("1 ", False),
        ("", False),
    ],
)
def test_adb_auto_repair_enabled_exact_semantics(monkeypatch, raw, expected):
    monkeypatch.setenv("STP_ADB_AUTO_REPAIR", raw)
    reset_agent_settings_caches()
    settings = get_heartbeat_settings()
    assert settings.stp_adb_auto_repair == raw  # 原值透传
    assert settings.adb_auto_repair_enabled is expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("true", True),
        ("TRUE", True),
        ("True", True),
        # 迁移前是 `.lower() == "true"`：以下都不算启用
        ("1", False),
        ("yes", False),
        ("on", False),
        ("false", False),
        (" true", False),
        ("", False),
    ],
)
def test_auto_register_enabled_semantics(monkeypatch, raw, expected):
    monkeypatch.setenv("AUTO_REGISTER_HOST", raw)
    reset_agent_settings_caches()
    assert get_registration_settings().auto_register_enabled is expected


def test_inverted_interval_clamp_only_warns(monkeypatch, caplog):
    """min > max：迁移前静默恒压到 min；迁移后只补一条 WARNING，不改值、不抛错。"""
    monkeypatch.setenv("STP_HEARTBEAT_INTERVAL_MIN", "200")
    monkeypatch.setenv("STP_HEARTBEAT_INTERVAL_MAX", "100")
    reset_agent_settings_caches()
    with caplog.at_level(logging.WARNING):
        settings = get_heartbeat_settings()
    assert settings.stp_heartbeat_interval_min == 200.0
    assert settings.stp_heartbeat_interval_max == 100.0
    assert any("STP_HEARTBEAT_INTERVAL_MIN" in record.message for record in caplog.records)


def test_env_override_and_cache(monkeypatch):
    monkeypatch.delenv("COORDINATOR_HEARTBEAT_INTERVAL", raising=False)
    assert get_heartbeat_settings().coordinator_heartbeat_interval == 30.0  # 预热缓存
    monkeypatch.setenv("COORDINATOR_HEARTBEAT_INTERVAL", "5")
    assert get_heartbeat_settings().coordinator_heartbeat_interval == 30.0  # 未 reset：缓存命中
    reset_agent_settings_caches()
    assert get_heartbeat_settings().coordinator_heartbeat_interval == 5.0  # reset 后新值


def test_dotenv_file_alone_must_not_take_effect(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("COORDINATOR_HEARTBEAT_INTERVAL=1\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    reset_agent_settings_caches()
    assert get_heartbeat_settings().coordinator_heartbeat_interval == 30.0


def test_reset_clears_registration_cache_too(monkeypatch):
    monkeypatch.setenv("AUTO_REGISTER_RETRY_DELAY", "3")
    reset_agent_settings_caches()
    assert get_registration_settings().auto_register_retry_delay == 3.0
    monkeypatch.setenv("AUTO_REGISTER_RETRY_DELAY", "7")
    assert get_registration_settings().auto_register_retry_delay == 3.0  # 缓存命中
    reset_agent_settings_caches()
    assert get_registration_settings().auto_register_retry_delay == 7.0


def test_heartbeat_thread_reads_settings(heartbeat_env):
    from backend.agent.heartbeat_thread import HeartbeatThread

    def _make():
        return HeartbeatThread(
            api_url="http://server",
            host_id="host-1",
            adb_path="adb",
            mount_points=[],
            host_info={},
            poll_interval=60,
            get_active_job_count=lambda: 0,
        )

    thread = _make()
    assert thread._min_poll_interval == 10.0
    assert thread._max_poll_interval == 120.0
    assert thread._adb_repair_cooldown == 300.0

    heartbeat_env.set("STP_HEARTBEAT_INTERVAL_MIN", "25.5")
    heartbeat_env.set("STP_HEARTBEAT_INTERVAL_MAX", "45")
    heartbeat_env.set("STP_ADB_REPAIR_COOLDOWN_SECONDS", "90")
    thread = _make()
    assert thread._min_poll_interval == 25.5
    assert thread._max_poll_interval == 45.0
    assert thread._adb_repair_cooldown == 90.0


def test_coordinator_reads_settings(heartbeat_env):
    from backend.agent.coordinator import HostRunCoordinator

    coordinator = HostRunCoordinator(
        api_url="http://server",
        host_id="host-1",
        agent_instance_id="inst-1",
    )
    assert coordinator._interval == 30.0
    assert coordinator._MAX_PLAN_RUN_HOST_PROJECTIONS == 200

    heartbeat_env.set("COORDINATOR_HEARTBEAT_INTERVAL", "12.5")
    heartbeat_env.set("COORDINATOR_MAX_PLAN_RUN_HOSTS", "50")
    coordinator = HostRunCoordinator(
        api_url="http://server",
        host_id="host-1",
        agent_instance_id="inst-1",
    )
    assert coordinator._interval == 12.5
    assert coordinator._MAX_PLAN_RUN_HOST_PROJECTIONS == 50


_PACING_KNOBS = (
    "COORDINATOR_HEARTBEAT_INTERVAL",
    "STP_HEARTBEAT_INTERVAL_MIN",
    "STP_HEARTBEAT_INTERVAL_MAX",
    "STP_ADB_REPAIR_COOLDOWN_SECONDS",
)


@pytest.mark.parametrize("env_name", _PACING_KNOBS)
@pytest.mark.parametrize("raw", ["0", "-5"])
def test_non_positive_pacing_clamped(monkeypatch, caplog, env_name, raw):
    """#2086：0/负值直接喂 Event.wait → 忙循环 → 钳到下限 + WARNING。"""
    monkeypatch.setenv(env_name, raw)
    reset_agent_settings_caches()
    with caplog.at_level(logging.WARNING):
        settings = get_heartbeat_settings()
    assert getattr(settings, env_name.lower()) == 1.0
    assert any(env_name in record.message for record in caplog.records)


def test_min_max_both_clamped_stays_ordered(monkeypatch):
    """守卫：MIN/MAX 同时为 0 时钳成相等的下限，不触发 min>max 告警路径。"""
    monkeypatch.setenv("STP_HEARTBEAT_INTERVAL_MIN", "0")
    monkeypatch.setenv("STP_HEARTBEAT_INTERVAL_MAX", "0")
    reset_agent_settings_caches()
    settings = get_heartbeat_settings()
    assert settings.stp_heartbeat_interval_min == settings.stp_heartbeat_interval_max == 1.0


def test_heartbeat_pacing_reload_takes_effect(heartbeat_env):
    """#2086：构造后改 env + reload → 实例级 re-apply 生效（原先只有重建实例才生效）。"""
    from backend.agent.heartbeat_thread import HeartbeatThread

    thread = HeartbeatThread(
        api_url="http://server",
        host_id="host-1",
        adb_path="adb",
        mount_points=[],
        host_info={},
        poll_interval=60,
        get_active_job_count=lambda: 0,
    )
    last_repair_at = thread._last_adb_repair_at

    heartbeat_env.set("STP_HEARTBEAT_INTERVAL_MIN", "25.5")
    heartbeat_env.set("STP_HEARTBEAT_INTERVAL_MAX", "45")
    heartbeat_env.set("STP_ADB_REPAIR_COOLDOWN_SECONDS", "90")
    assert thread._min_poll_interval == 10.0, "未 reload 前不得自行变化"

    thread.reload_from_settings()

    assert thread._min_poll_interval == 25.5
    assert thread._max_poll_interval == 45.0
    assert thread._adb_repair_cooldown == 90.0
    assert thread._last_adb_repair_at == last_repair_at, "冷却窗口不因重载作废"


def test_reload_from_settings_switches_shared_root_and_gates_capacity(heartbeat_env):
    """#4077 B1: reload_config 切换 STP_AEE_NFS_ROOT 到未挂载新根后心跳立刻降槽为 0。"""
    from backend.agent.capacity_reporter import compute_capacity
    from backend.agent.heartbeat_thread import HeartbeatThread
    from backend.agent.mount_health_probe import MountHealthProbe

    heartbeat_env.set("MOUNT_POINTS", "/mnt/old")
    heartbeat_env.set("STP_AEE_NFS_ROOT", "/mnt/old")
    thread = HeartbeatThread(
        api_url="http://server",
        host_id="host-1",
        adb_path="adb",
        mount_points=["/mnt/old"],
        host_info={},
        poll_interval=60,
        get_active_job_count=lambda: 0,
    )
    mountinfo_text = "1 0 0:1 / /mnt/old rw - nfs4 srv:/old rw\n"
    probe = MountHealthProbe(
        probe_fn=lambda _p: True,
        mountinfo_reader=lambda _path: mountinfo_text,
    )
    thread._mount_health_probe = probe

    before_status = probe.snapshot(thread._mount_points)
    before_cap = compute_capacity(
        active_job_count=0,
        active_device_count=0,
        online_healthy_devices=2,
        total_devices=2,
        system_stats={"cpu_load": 1.0, "ram_usage": 10.0, "disk_usage": {"writable_percent": 10.0}},
        mount_status=before_status,
    )
    assert before_cap["health"]["mount_ok"] is True
    assert before_cap["capacity"]["effective_slots"] == 2

    # 模拟 reload_config 将 STP_AEE_NFS_ROOT 切到未挂载的新根
    heartbeat_env.set("STP_AEE_NFS_ROOT", "/mnt/stp-log-new")
    thread.reload_from_settings()

    assert thread._mount_points == ["/mnt/old", "/mnt/stp-log-new"]
    after_status = probe.snapshot(thread._mount_points)
    after_cap = compute_capacity(
        active_job_count=0,
        active_device_count=0,
        online_healthy_devices=2,
        total_devices=2,
        system_stats={"cpu_load": 1.0, "ram_usage": 10.0, "disk_usage": {"writable_percent": 10.0}},
        mount_status=after_status,
    )
    assert after_status["/mnt/stp-log-new"]["ok"] is False
    assert after_cap["health"]["mount_ok"] is False
    assert after_cap["health"]["status"] == "UNSCHEDULABLE"
    assert after_cap["capacity"]["effective_slots"] == 0


def test_reload_config_inflight_tick_cannot_keep_slots_or_emit_stale_root(
    heartbeat_env, monkeypatch,
):
    """#4077 R2 B1: reload 切到未挂新根后立即清零普通槽位，且在途旧 tick 不得回写旧槽位或上报旧根。"""
    import threading
    from types import SimpleNamespace
    from backend.agent.heartbeat_thread import HeartbeatThread
    from backend.agent.mount_health_probe import MountHealthProbe

    heartbeat_env.set("MOUNT_POINTS", "/mnt/old")
    heartbeat_env.set("STP_AEE_NFS_ROOT", "/mnt/old")

    mountinfo_text = "1 0 0:1 / /mnt/old rw - nfs4 srv:/old rw\n"
    sent_payloads: list[dict] = []
    pause_after_snapshot = False
    snapshot_entered = threading.Event()
    snapshot_release = threading.Event()

    monkeypatch.setattr(
        "backend.agent.heartbeat_thread.device_discovery.discover_devices",
        lambda *_a: [
            {"serial": "dev-1", "adb_state": "device"},
            {"serial": "dev-2", "adb_state": "device"},
        ],
    )
    monkeypatch.setattr(
        HeartbeatThread,
        "_collect_device_infos",
        lambda _self, devs: [
            {"adb_state": "device", "adb_connected": True} for _ in devs
        ],
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
        lambda: (2, 1),
    )
    monkeypatch.setattr(
        "backend.agent.heartbeat_thread.device_discovery.count_adb_interface_devices",
        lambda: 2,
    )
    monkeypatch.setattr(
        "backend.agent.heartbeat_thread.read_usb_power_presence",
        lambda: None,
    )
    monkeypatch.setattr(
        "backend.agent.system_monitor.collect_system_stats",
        lambda: {
            "cpu_load": 1.0,
            "ram_usage": 10.0,
            "disk_usage": {"writable_percent": 10.0},
        },
    )
    monkeypatch.setattr(
        "backend.agent.heartbeat_thread.send_heartbeat",
        lambda *_a, **kwargs: sent_payloads.append(kwargs) or {"ok": True},
    )

    thread = HeartbeatThread(
        api_url="http://server",
        host_id="host-1",
        adb_path="adb",
        mount_points=["/mnt/old"],
        host_info={},
        poll_interval=60,
        get_active_job_count=lambda: 0,
    )
    probe = MountHealthProbe(
        probe_fn=lambda _p: True,
        mountinfo_reader=lambda _path: mountinfo_text,
    )
    orig_snapshot = probe.snapshot

    def gated_snapshot(points):
        res = orig_snapshot(points)
        if pause_after_snapshot and points == ["/mnt/old"]:
            snapshot_entered.set()
            assert snapshot_release.wait(5.0)
        return res

    probe.snapshot = gated_snapshot  # type: ignore[method-assign]
    thread._mount_health_probe = probe
    thread._kernel_usb_watch = SimpleNamespace(
        poll=lambda **_k: [],
        channel_state=lambda: "ok",
    )

    # 第 1 拍建立普通槽位 2
    thread._tick()
    assert thread.effective_slots == 2
    assert len(sent_payloads) == 1

    # 第 2 拍在完成旧列表 snapshot() 后暂停，期间触发 reload 切到未挂新根
    pause_after_snapshot = True
    tick_thread = threading.Thread(target=thread._tick, daemon=True)
    tick_thread.start()
    assert snapshot_entered.wait(2.0)

    heartbeat_env.set("STP_AEE_NFS_ROOT", "/mnt/stp-log-new")
    thread.reload_from_settings()
    # ACK 当刻普通槽位已立即归零（不待下一拍），不让在途认领按 capacity=2 发起
    assert thread.effective_slots == 0

    pause_after_snapshot = False
    snapshot_release.set()
    tick_thread.join(timeout=3.0)
    assert not tick_thread.is_alive()

    assert thread.effective_slots == 0
    assert len(sent_payloads) == 2
    latest = sent_payloads[-1]
    assert "/mnt/stp-log-new" in latest["mount_status"]
    assert latest["mount_status"]["/mnt/stp-log-new"]["ok"] is False
    assert latest["health"]["mount_ok"] is False
    assert latest["capacity"]["effective_slots"] == 0


def test_reload_mount_points_succeeds_even_with_invalid_pacing_knob(
    heartbeat_env, caplog,
):
    """#4077 R2 B1: 非法心跳节奏旋钮不得阻断挂载点刷新、探针 reset 与槽位清零。"""
    import logging
    from backend.agent.heartbeat_thread import HeartbeatThread
    from backend.agent.mount_health_probe import MountHealthProbe

    heartbeat_env.set("MOUNT_POINTS", "/mnt/old")
    heartbeat_env.set("STP_AEE_NFS_ROOT", "/mnt/old")
    thread = HeartbeatThread(
        api_url="http://server",
        host_id="host-1",
        adb_path="adb",
        mount_points=["/mnt/old"],
        host_info={},
        poll_interval=60,
        get_active_job_count=lambda: 0,
    )
    thread._mount_health_probe = MountHealthProbe(
        probe_fn=lambda _p: True,
        mountinfo_reader=lambda _p: "1 0 0:1 / /mnt/old rw - nfs4 srv:/old rw\n",
    )
    with thread._capacity_lock:
        thread._effective_slots = 2
    before_min = thread._min_poll_interval

    heartbeat_env.set("STP_HEARTBEAT_INTERVAL_MIN", "not-a-number")
    heartbeat_env.set("STP_AEE_NFS_ROOT", "/mnt/stp-log-new")
    with caplog.at_level(logging.ERROR):
        thread.reload_from_settings()

    assert thread._min_poll_interval == before_min
    assert any("heartbeat_pacing_reload_skipped" in r.message for r in caplog.records)
    assert thread._mount_points == ["/mnt/old", "/mnt/stp-log-new"]
    assert thread.effective_slots == 0


def test_reload_from_settings_zeroes_slots_when_shared_root_switches_within_same_list(
    heartbeat_env,
):
    """#4077 R3 B1: 监控列表不变仅切换 STP_AEE_NFS_ROOT 时，仍递增代际、reset 探针并归零槽位。"""
    from backend.agent.heartbeat_thread import HeartbeatThread
    from backend.agent.mount_health_probe import MountHealthProbe

    heartbeat_env.set("MOUNT_POINTS", "/mnt/old,/mnt/new")
    heartbeat_env.set("STP_AEE_NFS_ROOT", "/mnt/old")
    thread = HeartbeatThread(
        api_url="http://server",
        host_id="host-1",
        adb_path="adb",
        mount_points=["/mnt/old", "/mnt/new"],
        host_info={},
        poll_interval=60,
        get_active_job_count=lambda: 0,
    )
    thread._mount_health_probe = MountHealthProbe(
        probe_fn=lambda _p: True,
        mountinfo_reader=lambda _p: "1 0 0:1 / /mnt/old rw - nfs4 srv:/old rw\n",
    )
    with thread._capacity_lock:
        thread._effective_slots = 2
    before_gen = thread._mount_points_generation

    heartbeat_env.set("STP_AEE_NFS_ROOT", "/mnt/new")
    thread.reload_from_settings()

    assert thread._mount_points == ["/mnt/old", "/mnt/new"]
    assert thread._mount_points_generation == before_gen + 1
    assert thread.effective_slots == 0


def test_heartbeat_tick_rechecks_generation_after_artifact_digest_and_before_ws(
    heartbeat_env, monkeypatch,
):
    """#4077 R3 B1: artifact_digest 回调期间若切根，HTTP 不得携带旧根 ok=true 且 WS 不推送旧根。"""
    import threading
    from types import SimpleNamespace
    from backend.agent.heartbeat_thread import HeartbeatThread
    from backend.agent.mount_health_probe import MountHealthProbe

    heartbeat_env.set("MOUNT_POINTS", "/mnt/old")
    heartbeat_env.set("STP_AEE_NFS_ROOT", "/mnt/old")

    sent_payloads: list[dict] = []
    ws_payloads: list[dict] = []
    digest_entered = threading.Event()
    digest_release = threading.Event()
    pause_on_digest = True

    monkeypatch.setattr(
        "backend.agent.heartbeat_thread.device_discovery.discover_devices",
        lambda *_a: [
            {"serial": "dev-1", "adb_state": "device"},
            {"serial": "dev-2", "adb_state": "device"},
        ],
    )
    monkeypatch.setattr(
        HeartbeatThread,
        "_collect_device_infos",
        lambda _self, devs: [
            {"adb_state": "device", "adb_connected": True} for _ in devs
        ],
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
        lambda: (2, 1),
    )
    monkeypatch.setattr(
        "backend.agent.heartbeat_thread.device_discovery.count_adb_interface_devices",
        lambda: 2,
    )
    monkeypatch.setattr(
        "backend.agent.heartbeat_thread.read_usb_power_presence",
        lambda: None,
    )
    monkeypatch.setattr(
        "backend.agent.system_monitor.collect_system_stats",
        lambda: {
            "cpu_load": 1.0,
            "ram_usage": 10.0,
            "disk_usage": {"writable_percent": 10.0},
        },
    )
    monkeypatch.setattr(
        "backend.agent.heartbeat_thread.send_heartbeat",
        lambda *_a, **kwargs: sent_payloads.append(kwargs) or {"ok": True},
    )

    def digest_cb():
        if pause_on_digest:
            digest_entered.set()
            assert digest_release.wait(5.0)
        return "dig-1"

    ws_client = SimpleNamespace(
        connected=True,
        send_heartbeat=lambda stats: ws_payloads.append(stats),
    )
    thread = HeartbeatThread(
        api_url="http://server",
        host_id="host-1",
        adb_path="adb",
        mount_points=["/mnt/old"],
        host_info={},
        poll_interval=60,
        sio_client=ws_client,
        agent_artifact_digest=digest_cb,
        get_active_job_count=lambda: 0,
    )
    thread._mount_health_probe = MountHealthProbe(
        probe_fn=lambda _p: True,
        mountinfo_reader=lambda _p: "1 0 0:1 / /mnt/old rw - nfs4 srv:/old rw\n",
    )
    thread._kernel_usb_watch = SimpleNamespace(
        poll=lambda **_k: [],
        channel_state=lambda: "ok",
    )

    t = threading.Thread(target=thread._tick, daemon=True)
    t.start()
    assert digest_entered.wait(2.0)

    heartbeat_env.set("STP_AEE_NFS_ROOT", "/mnt/stp-log-new")
    thread.reload_from_settings()
    pause_on_digest = False
    digest_release.set()
    t.join(timeout=3.0)
    assert not t.is_alive()

    assert len(sent_payloads) == 1
    assert sent_payloads[0]["mount_status"]["/mnt/stp-log-new"]["ok"] is False
    assert sent_payloads[0]["health"]["mount_ok"] is False
    assert sent_payloads[0]["capacity"]["effective_slots"] == 0
    assert len(ws_payloads) == 1
    assert "/mnt/stp-log-new" in ws_payloads[0]["mount_status"]
    assert ws_payloads[0]["mount_status"]["/mnt/stp-log-new"]["ok"] is False


def test_coordinator_pacing_reload_takes_effect(heartbeat_env):
    from backend.agent.coordinator import HostRunCoordinator

    coordinator = HostRunCoordinator(
        api_url="http://server",
        host_id="host-1",
        agent_instance_id="inst-1",
    )
    heartbeat_env.set("COORDINATOR_HEARTBEAT_INTERVAL", "12.5")
    heartbeat_env.set("COORDINATOR_MAX_PLAN_RUN_HOSTS", "50")
    assert coordinator._interval == 30.0

    coordinator.reload_from_settings()

    assert coordinator._interval == 12.5
    assert coordinator._MAX_PLAN_RUN_HOST_PROJECTIONS == 50


def test_main_reload_config_reapplies_pacing():
    """静态契约：#2086 的实例级 re-apply 必须留在 reload_config 分支内（防回退）。"""
    src = (Path(__file__).resolve().parents[1] / "control_handler.py").read_text(
        encoding="utf-8"
    )
    branch = src.split('elif command == "reload_config":', 1)[1]
    branch = branch.split("elif command ==", 1)[0]  # 截到下一个分支为止
    assert "reset_agent_settings_caches()" in branch, "重读 .env 后必须清缓存（否则 re-apply 读旧值）"
    assert "heartbeat_thread.reload_from_settings()" in branch
    assert "coordinator.reload_from_settings()" in branch


def test_registration_settings_read_stays_deferred():
    """静态契约：注册域取值点须保持迁移前的惰性时机。

    迁移前 `AUTO_REGISTER_*` 只在「HOST_ID 非法」与「自动注册模式」两个分支内解析；
    若 Settings 化把 `get_registration_settings()` 提到无条件路径，HOST_ID 正常的
    机器会被用不到的注册旋钮（非法值）拖垮启动——失败面被扩大。

    #736：HOST_ID 解析迁到 ``startup_identity``；契约跟随实现文件。
    """
    src = (
        Path(__file__).resolve().parents[1] / "startup_identity.py"
    ).read_text(encoding="utf-8")
    idx_load = src.find("host_id = load_required_host_id()")
    idx_first_read = src.find("get_registration_settings()")
    assert idx_load > 0, "startup_identity.py 里找不到 HOST_ID 加载点"
    assert idx_first_read > idx_load, (
        "get_registration_settings() 不得出现在 load_required_host_id 之前（无条件路径）"
    )
    # 两次取值点在两个分支内（except 分支 + host_id is None 分支）
    assert src.count("get_registration_settings()") == 2


# ── #2279：坏旋钮不得让心跳静默中断 ─────────────────────────────────────────


def _make_thread_for_tick(ht, monkeypatch=None):
    """构造一个可直接跑 `_safe_tick()` 的 HeartbeatThread（#2279 用）。

    tick 会遍历设备并采集容量/健康；此处不关心这些，统一桩成「零设备」，
    使断言聚焦在「条件表达式是否抛 → 心跳是否发出」。
    """
    from backend.agent.heartbeat_thread import HeartbeatThread

    thread = HeartbeatThread(
        api_url="http://server",
        host_id="host-1",
        adb_path="adb",
        mount_points=[],
        host_info={},
        poll_interval=60,
        get_active_job_count=lambda: 0,
    )
    # 零设备：discover 返回空列表 → 不进入每设备采集路径
    thread._adb_path = "adb"
    return thread



def test_tick_survives_invalid_pacing_knob_when_adb_conflict(heartbeat_env, monkeypatch):
    """#2279 主症状：非数值旋钮 + ADB 冲突 → 心跳仍须发出。

    修复前：`_tick` 在条件表达式里构造 `HeartbeatSettings` → pydantic
    `ValidationError` → `_safe_tick` 吞掉 → **该 tick 完全不发心跳**（进程仍活着，
    表现为静默掉线）。
    """
    from backend.agent import heartbeat_thread as ht

    thread = _make_thread_for_tick(ht)

    # ADB fork server 冲突（短路求值：只有它为真才会读 Settings）
    monkeypatch.setattr(ht.device_discovery, "get_adb_server_port", lambda: 5037)
    monkeypatch.setattr(
        ht.device_discovery, "list_adb_fork_servers",
        lambda: [{"port": 5038}],
    )
    # 非数值节奏旋钮（严格组 → ValidationError）
    heartbeat_env.set("STP_HEARTBEAT_INTERVAL_MIN", "abc")
    heartbeat_env.set("STP_ADB_AUTO_REPAIR", "1")

    sent = []
    monkeypatch.setattr(ht, "send_heartbeat", lambda *a, **k: sent.append(1) or {"ok": True})

    thread._safe_tick()

    assert sent, "坏旋钮 + ADB 冲突时心跳被静默中断（#2279）"


def test_tick_still_sends_heartbeat_without_adb_conflict(heartbeat_env, monkeypatch):
    """#2279 负向对照：无 ADB 冲突时坏旋钮本就不该影响心跳（短路未读 Settings）。"""
    from backend.agent import heartbeat_thread as ht

    thread = _make_thread_for_tick(ht)
    monkeypatch.setattr(ht.device_discovery, "get_adb_server_port", lambda: 5037)
    monkeypatch.setattr(ht.device_discovery, "list_adb_fork_servers", lambda: [])
    heartbeat_env.set("STP_HEARTBEAT_INTERVAL_MIN", "abc")

    sent = []
    monkeypatch.setattr(ht, "send_heartbeat", lambda *a, **k: sent.append(1) or {"ok": True})

    thread._safe_tick()

    assert sent, "无 ADB 冲突时心跳不应受坏旋钮影响"


def test_heartbeat_reload_survives_invalid_knob(heartbeat_env, caplog):
    """#2279 验收②：坏旋钮时 `reload_from_settings` 不抛，沿用既有值并记 ERROR。

    该方法在 `reload_config` 处理器里**无外层兜底**——抛出会让整个 reload 失败，
    连带 coordinator 与后续步骤都不执行。
    """
    import logging
    from backend.agent.heartbeat_thread import HeartbeatThread

    thread = HeartbeatThread(
        api_url="http://server",
        host_id="host-1",
        adb_path="adb",
        mount_points=[],
        host_info={},
        poll_interval=60,
        get_active_job_count=lambda: 0,
    )
    before = (thread._min_poll_interval, thread._max_poll_interval, thread._adb_repair_cooldown)

    heartbeat_env.set("STP_HEARTBEAT_INTERVAL_MIN", "abc")
    with caplog.at_level(logging.ERROR):
        thread.reload_from_settings()          # 不得抛

    assert (thread._min_poll_interval, thread._max_poll_interval,
            thread._adb_repair_cooldown) == before, "坏配置下应沿用既有值"
    assert any("heartbeat_pacing_reload_skipped" in r.message or
               "heartbeat_settings_unavailable" in r.message for r in caplog.records), \
        "失败须留可观测日志（验收②：不再只留 debug）"


def test_coordinator_reload_survives_invalid_knob(heartbeat_env, caplog):
    """#2279：coordinator 侧同源暴露（同一 reload 处理器内、紧随 heartbeat 之后）。"""
    import logging
    from backend.agent.coordinator import HostRunCoordinator

    coordinator = HostRunCoordinator(
        api_url="http://server", host_id="host-1", agent_instance_id="inst-1",
    )
    before = (coordinator._interval, coordinator._MAX_PLAN_RUN_HOST_PROJECTIONS)

    heartbeat_env.set("COORDINATOR_HEARTBEAT_INTERVAL", "abc")
    with caplog.at_level(logging.ERROR):
        coordinator.reload_from_settings()     # 不得抛

    assert (coordinator._interval, coordinator._MAX_PLAN_RUN_HOST_PROJECTIONS) == before
    assert any("coordinator_pacing_reload_skipped" in r.message for r in caplog.records)
