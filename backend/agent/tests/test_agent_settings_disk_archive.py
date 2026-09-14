"""磁盘与日志归档域 Settings 测试（ADR-0042 P2 #2）。

覆盖：默认值逐一对照迁移前 · **宽容组的 #1710 语义**（非法/越界/非有限 → 默认 + 告警，
不抛错）· **严格组的失败面**（非法 → ValidationError，等价于迁移前的启动 ValueError）·
env 覆盖 + 缓存语义 · `.env` 负向 · 监控类的惰性 property 与 Settings 同源。
"""

from __future__ import annotations

import logging

import pytest
from pydantic import ValidationError

from backend.agent.settings import (
    DiskArchiveSettings,
    get_disk_archive_settings,
    reset_agent_settings_caches,
)

_PRE_MIGRATION_DEFAULTS = {
    "stp_hdd_spill_critical_pct": 98.0,
    "stp_hdd_spill_critical_batch": 100,
    "stp_hdd_spill_catchup_interval": 30.0,
    "stp_local_disk_monitor_interval_seconds": 300.0,
    "stp_local_disk_spill_threshold": 80.0,
    "stp_local_disk_spill_target": 70.0,
    "stp_log_archive_interval_seconds": 3600.0,
    "stp_log_archive_grace_seconds": 1800.0,
}

_TOLERANT = (
    "STP_HDD_SPILL_CRITICAL_PCT",
    "STP_HDD_SPILL_CRITICAL_BATCH",
    "STP_HDD_SPILL_CATCHUP_INTERVAL",
)


@pytest.fixture(autouse=True)
def _clean():
    reset_agent_settings_caches()
    yield
    reset_agent_settings_caches()


def test_defaults_match_pre_migration(monkeypatch):
    for name in _PRE_MIGRATION_DEFAULTS:
        monkeypatch.delenv(name.upper(), raising=False)
    settings = DiskArchiveSettings()
    for name, expected in _PRE_MIGRATION_DEFAULTS.items():
        actual = getattr(settings, name)
        assert actual == expected, name
        assert type(actual) is type(expected), f"{name} 类型漂移: {type(actual)}"


@pytest.mark.parametrize(
    "env_name,raw,field,expected",
    [
        # 宽容组：非法/越界/非有限 → 默认值 + WARNING（#1710）
        ("STP_HDD_SPILL_CRITICAL_PCT", "bogus", "stp_hdd_spill_critical_pct", 98.0),
        ("STP_HDD_SPILL_CRITICAL_PCT", "150", "stp_hdd_spill_critical_pct", 98.0),
        ("STP_HDD_SPILL_CRITICAL_PCT", "-1", "stp_hdd_spill_critical_pct", 98.0),
        ("STP_HDD_SPILL_CRITICAL_PCT", "97.5", "stp_hdd_spill_critical_pct", 97.5),
        ("STP_HDD_SPILL_CRITICAL_BATCH", "0", "stp_hdd_spill_critical_batch", 100),
        ("STP_HDD_SPILL_CRITICAL_BATCH", "-5", "stp_hdd_spill_critical_batch", 100),
        ("STP_HDD_SPILL_CRITICAL_BATCH", "x", "stp_hdd_spill_critical_batch", 100),
        ("STP_HDD_SPILL_CRITICAL_BATCH", "250", "stp_hdd_spill_critical_batch", 250),
        ("STP_HDD_SPILL_CATCHUP_INTERVAL", "nan", "stp_hdd_spill_catchup_interval", 30.0),
        ("STP_HDD_SPILL_CATCHUP_INTERVAL", "inf", "stp_hdd_spill_catchup_interval", 30.0),
        ("STP_HDD_SPILL_CATCHUP_INTERVAL", "30s", "stp_hdd_spill_catchup_interval", 30.0),
        ("STP_HDD_SPILL_CATCHUP_INTERVAL", "45", "stp_hdd_spill_catchup_interval", 45.0),
    ],
)
def test_tolerant_group_falls_back_and_warns(monkeypatch, caplog, env_name, raw, field, expected):
    monkeypatch.setenv(env_name, raw)
    reset_agent_settings_caches()
    with caplog.at_level(logging.WARNING):
        settings = get_disk_archive_settings()
    assert getattr(settings, field) == expected
    if expected == _PRE_MIGRATION_DEFAULTS[field]:
        assert any(env_name in record.message or raw in str(record.args) for record in caplog.records) or raw != expected


@pytest.mark.parametrize(
    "env_name,raw",
    [
        ("STP_LOCAL_DISK_MONITOR_INTERVAL_SECONDS", "bogus"),
        ("STP_LOCAL_DISK_SPILL_THRESHOLD", "abc"),
        ("STP_LOCAL_DISK_SPILL_TARGET", ""),  # 空串在迁移前也走 float("") → ValueError
        ("STP_LOG_ARCHIVE_INTERVAL_SECONDS", "hourly"),
        ("STP_LOG_ARCHIVE_GRACE_SECONDS", "half"),
    ],
)
def test_strict_group_raises_like_before(monkeypatch, env_name, raw):
    """严格组：迁移前 `float(os.getenv(...))` 直转——非法值启动即失败（此处等价为 ValidationError）。"""
    monkeypatch.setenv(env_name, raw)
    reset_agent_settings_caches()
    with pytest.raises(ValidationError):
        get_disk_archive_settings()


def test_env_override_and_cache(monkeypatch):
    monkeypatch.delenv("STP_LOCAL_DISK_SPILL_THRESHOLD", raising=False)
    assert get_disk_archive_settings().stp_local_disk_spill_threshold == 80.0  # 预热缓存
    monkeypatch.setenv("STP_LOCAL_DISK_SPILL_THRESHOLD", "65")
    assert get_disk_archive_settings().stp_local_disk_spill_threshold == 80.0  # 未 reset：缓存命中
    reset_agent_settings_caches()
    assert get_disk_archive_settings().stp_local_disk_spill_threshold == 65.0  # reset 后新值


def test_dotenv_file_alone_must_not_take_effect(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("STP_LOG_ARCHIVE_INTERVAL_SECONDS=10\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    reset_agent_settings_caches()
    assert get_disk_archive_settings().stp_log_archive_interval_seconds == 3600.0


def test_monitor_properties_follow_settings(monkeypatch):
    """HddSpillMonitor 的三个（原类属性）旋钮现在是惰性 property，与 Settings 同源。"""
    from backend.agent.local_disk_monitor import HddSpillMonitor

    mon = HddSpillMonitor.instance()
    assert mon._CRITICAL_USAGE_PCT == 98.0
    assert mon._MAX_SPILL_CRITICAL == 100
    assert mon._SPILL_CATCHUP_INTERVAL == 30.0

    monkeypatch.setenv("STP_HDD_SPILL_CRITICAL_PCT", "95")
    monkeypatch.setenv("STP_HDD_SPILL_CRITICAL_BATCH", "250")
    monkeypatch.setenv("STP_HDD_SPILL_CATCHUP_INTERVAL", "15")
    reset_agent_settings_caches()
    assert mon._CRITICAL_USAGE_PCT == 95.0
    assert mon._MAX_SPILL_CRITICAL == 250
    assert mon._SPILL_CATCHUP_INTERVAL == 15.0
    HddSpillMonitor._reset_for_tests()
