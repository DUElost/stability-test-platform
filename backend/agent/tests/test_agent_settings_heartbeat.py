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


def test_registration_settings_read_stays_deferred():
    """静态契约：注册域取值点须保持迁移前的惰性时机。

    迁移前 `AUTO_REGISTER_*` 只在「HOST_ID 非法」与「自动注册模式」两个分支内解析；
    若 Settings 化把 `get_registration_settings()` 提到无条件路径，HOST_ID 正常的
    机器会被用不到的注册旋钮（非法值）拖垮启动——失败面被扩大。
    """
    src = (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")
    idx_load = src.find("host_id = load_required_host_id()")
    idx_first_read = src.find("get_registration_settings()")
    assert idx_load > 0, "main.py 里找不到 HOST_ID 加载点"
    assert idx_first_read > idx_load, (
        "get_registration_settings() 不得出现在 load_required_host_id 之前（无条件路径）"
    )
    # 两次取值点在两个分支内（except 分支 + host_id is None 分支）
    assert src.count("get_registration_settings()") == 2
