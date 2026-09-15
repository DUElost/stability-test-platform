"""租约域 Settings 等价性与热更新测试（ADR-0042 P1 试点，agent 侧）。

覆盖：
1. **等价性**：默认值与迁移前 `int/float(os.getenv(...))` 逐一对齐（含 TTL 来源）；
2. **env 覆盖 + 缓存语义**：写入生效需伴随 `reset_agent_settings_caches()`；
3. **非法值** → `ValidationError`；
4. **来源契约**：只写 `.env` 文件不生效（`env_file=None`）；
5. **热更新闭环**：`main._reload_runtime_env()` 重读 `.env` → 缓存被清 → 新值可见
   （对应 hot-update 的 `reload_config` 路径）。
"""

from __future__ import annotations

import os

import pytest
from pydantic import ValidationError

from backend.agent.settings import (
    LeaseSettings,
    get_lease_settings,
    reset_agent_settings_caches,
)

_PRE_MIGRATION_DEFAULTS = {
    "agent_post_retries": 3,
    "agent_post_retry_base_delay": 1.0,
    "agent_lock_renewal_interval": 60,
    "agent_lease_extend_batch_chunk": 100,
    "agent_lease_ttl": 600,  # 迁移前 _BACKEND_LEASE_TTL
}


@pytest.fixture(autouse=True)
def _clean_cache():
    reset_agent_settings_caches()
    yield
    reset_agent_settings_caches()


def test_defaults_match_pre_migration_values(monkeypatch):
    for name in _PRE_MIGRATION_DEFAULTS:
        monkeypatch.delenv(name.upper(), raising=False)
    settings = LeaseSettings()
    for name, expected in _PRE_MIGRATION_DEFAULTS.items():
        actual = getattr(settings, name)
        assert actual == expected, name
        assert type(actual) is type(expected), f"{name} 类型漂移: {type(actual)}"


def test_env_override_requires_cache_reset(monkeypatch):
    monkeypatch.delenv("AGENT_LEASE_TTL", raising=False)
    assert get_lease_settings().agent_lease_ttl == 600  # 先预热缓存（默认值）
    monkeypatch.setenv("AGENT_LEASE_TTL", "900")
    assert get_lease_settings().agent_lease_ttl == 600  # 未 reset：缓存命中旧值
    reset_agent_settings_caches()
    assert get_lease_settings().agent_lease_ttl == 900  # reset 后读到新值


def test_invalid_value_raises_validation_error(monkeypatch):
    monkeypatch.setenv("AGENT_POST_RETRIES", "many")
    reset_agent_settings_caches()
    with pytest.raises(ValidationError):
        get_lease_settings()


@pytest.mark.parametrize("raw", ["0", "-5"])
def test_non_positive_renewal_interval_clamped(monkeypatch, caplog, raw):
    """#2086：续租节奏 0/负值 = 空转 → 钳到下限 + WARNING（非数值仍严格失败）。"""
    import logging

    monkeypatch.setenv("AGENT_LOCK_RENEWAL_INTERVAL", raw)
    reset_agent_settings_caches()
    with caplog.at_level(logging.WARNING):
        settings = get_lease_settings()
    assert settings.agent_lock_renewal_interval == 1
    assert type(settings.agent_lock_renewal_interval) is int
    assert any(
        "AGENT_LOCK_RENEWAL_INTERVAL" in record.message for record in caplog.records
    )


def test_non_numeric_renewal_interval_still_strict(monkeypatch):
    """守卫：#2086 只收 0/负值，**不放宽**类型严格性（非法值仍是 ValidationError）。"""
    monkeypatch.setenv("AGENT_LOCK_RENEWAL_INTERVAL", "1m")
    reset_agent_settings_caches()
    with pytest.raises(ValidationError):
        get_lease_settings()


def test_dotenv_file_alone_must_not_take_effect(tmp_path, monkeypatch):
    """负向用例（ADR-0042 v1.0）：只写 .env、不设进程 env → 不生效。"""
    (tmp_path / ".env").write_text("AGENT_LEASE_TTL=999\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AGENT_LEASE_TTL", raising=False)
    reset_agent_settings_caches()
    assert get_lease_settings().agent_lease_ttl == 600


def test_reload_runtime_env_clears_settings_cache(tmp_path):
    """热更新闭环：main._reload_runtime_env() 之后新值对 Settings 可见。

    `load_dotenv(override=True)` 直接写 os.environ（绕过 monkeypatch），
    故本用例自行快照/恢复环境变量。
    """
    from backend.agent.main import _reload_runtime_env

    env_file = tmp_path / "agent.env"
    env_file.write_text("AGENT_LEASE_TTL=777\nAGENT_LEASE_EXTEND_BATCH_CHUNK=5\n", encoding="utf-8")

    assert get_lease_settings().agent_lease_ttl == 600
    snapshot = {k: os.environ.get(k) for k in ("AGENT_LEASE_TTL", "AGENT_LEASE_EXTEND_BATCH_CHUNK")}
    try:
        assert _reload_runtime_env(env_file) is True
        # reload_config 路径在重读后调用 reset（本用例直接验证闭环语义）
        reset_agent_settings_caches()
        settings = get_lease_settings()
        assert settings.agent_lease_ttl == 777
        assert settings.agent_lease_extend_batch_chunk == 5
    finally:
        for key, value in snapshot.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        reset_agent_settings_caches()


def test_reset_helper_is_wired_into_reload_config_path():
    """静态契约：main.py 的 reload_config 分支必须调用 reset（防回退）。"""
    from pathlib import Path

    main_src = Path("backend/agent/main.py").read_text(encoding="utf-8")
    assert "reset_agent_settings_caches()" in main_src
    reload_idx = main_src.index('elif command == "reload_config":')
    window = main_src[reload_idx : reload_idx + 400]
    assert "_reload_runtime_env()" in window, "reload_config 分支应重读 .env"
    assert "reset_agent_settings_caches()" in window, "重读后必须清 Settings 缓存"
