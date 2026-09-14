"""调度域 Settings 等价性测试（ADR-0042 P1 试点）。

覆盖三类验收：
1. **等价性**：默认值与迁移前 `int/float(os.getenv(..., "<默认>"))` 逐一对齐；
2. **env 覆盖与校验**：覆盖生效；非法值抛出 `ValidationError`（迁移前是 import 期
   `ValueError`，同为「响亮的失败」）；
3. **来源契约**：`env_file=None` —— 只写 `.env` 文件、不设进程 env 时**不得生效**
   （`.env` 的来源与优先级仍由 `backend/core/env_source.py` 独家决定）。
"""

from __future__ import annotations

import os

# backend.core 导入链在缺 DATABASE_URL 时会拒绝导入（root tests 无 conftest 注入）。
# 先例：tests/metrics_registry.py（CI 已设 dummy URL，setdefault 不覆盖）。
os.environ.setdefault("DATABASE_URL", "sqlite:///./test-settings-scheduler.db")

import pytest
from pydantic import ValidationError

from backend.core.settings.scheduler import (
    SchedulerSettings,
    get_scheduler_settings,
    reset_scheduler_settings_cache,
)

# 迁移前默认值（逐一对齐；改动这些值等于改运维默认，必须走 Settings 模块）
_PRE_MIGRATION_DEFAULTS = {
    "run_recycle_interval_seconds": 30,
    "session_watchdog_interval_seconds": 15,
    "reconciler_interval_seconds": 15,
    "cron_poll_interval": 30.0,
    "retention_cleanup_interval_seconds": 3600,
    "queue_depth_poll_interval_seconds": 15,
    "precheck_reaper_interval_seconds": 45,
    "chain_reconciler_interval_seconds": 60,
    "revoked_token_cleanup_interval_seconds": 24 * 3600,
    "auto_archive_poll_interval_seconds": 120,
    "stp_admission_pump_interval_seconds": 5,
    "stp_counter_reconcile_interval_seconds": 300,
    "stp_signal_link_reconcile_interval_seconds": 300,
    "recycler_batch_size": 200,
    "artifact_retention_days": 30,
    "patrol_stall_batch_limit": 100,
    "coordinator_heartbeat_timeout_seconds": 300,
    "post_completion_grace_seconds": 120,
    "post_completion_max_defer_seconds": 6 * 3600,
    "plan_run_retention_days": 3,
    "schedule_dedup_window_seconds": 60.0,
}


@pytest.fixture(autouse=True)
def _clean_cache():
    reset_scheduler_settings_cache()
    yield
    reset_scheduler_settings_cache()


def test_defaults_match_pre_migration_literals(monkeypatch):
    """不设任何 env 时，字段默认值与迁移前常量逐一相等（含类型）。"""
    for name in _PRE_MIGRATION_DEFAULTS:
        monkeypatch.delenv(name.upper(), raising=False)
    settings = SchedulerSettings()
    for name, expected in _PRE_MIGRATION_DEFAULTS.items():
        actual = getattr(settings, name)
        assert actual == expected, name
        assert type(actual) is type(expected), f"{name} 类型漂移: {type(actual)}"


def test_env_override_takes_effect_after_reset(monkeypatch):
    monkeypatch.setenv("RECYCLER_BATCH_SIZE", "7")
    monkeypatch.setenv("CRON_POLL_INTERVAL", "2.5")
    reset_scheduler_settings_cache()
    settings = get_scheduler_settings()
    assert settings.recycler_batch_size == 7
    assert settings.cron_poll_interval == 2.5


def test_cache_is_stale_until_reset(monkeypatch):
    """惰性缓存的既定语义：改 env 后不 reset 仍读旧值；reset 后读到新值。"""
    assert get_scheduler_settings().recycler_batch_size == 200
    monkeypatch.setenv("RECYCLER_BATCH_SIZE", "11")
    assert get_scheduler_settings().recycler_batch_size == 200  # 缓存命中
    reset_scheduler_settings_cache()
    assert get_scheduler_settings().recycler_batch_size == 11


def test_invalid_value_raises_validation_error(monkeypatch):
    monkeypatch.setenv("RECYCLER_BATCH_SIZE", "not-an-int")
    reset_scheduler_settings_cache()
    with pytest.raises(ValidationError):
        get_scheduler_settings()


def test_all_field_names_map_to_env_names():
    """D3 名字不变：无 validation_alias 的字段，其 env 名 = 字段名大写。"""

    aliases = {"model_config"}
    for name in SchedulerSettings.model_fields:
        if name in aliases:
            continue
        json_schema = SchedulerSettings.model_json_schema()
        assert name in json_schema["properties"], name


def test_dotenv_file_alone_must_not_take_effect(tmp_path, monkeypatch):
    """负向用例（ADR-0042 v1.0 裁决附加）：只写 .env 文件、不设进程 env → 不生效。"""
    (tmp_path / ".env").write_text("RECYCLER_BATCH_SIZE=999\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("RECYCLER_BATCH_SIZE", raising=False)
    reset_scheduler_settings_cache()
    assert get_scheduler_settings().recycler_batch_size == 200


def test_domain_settings_does_not_read_env_file():
    """基类配置契约：env_file 必须为 None（禁止第二个 dotenv 来源）。"""
    from backend.core.settings.base import DomainSettings

    assert DomainSettings.model_config.get("env_file") is None
