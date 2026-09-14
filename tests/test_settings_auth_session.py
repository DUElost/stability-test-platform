"""安全与会话域 Settings 等价性测试（ADR-0042 P2 第一优先域）。

覆盖：默认值逐一对照迁移前、派生语义（secure/samesite/register/csrf）、
env 覆盖 + 缓存语义、惰性模块属性、`.env` 文件不生效（负向）、
以及生产 guard 与 CORS 护栏的既有行为（错误类型/文案不变）。
"""

from __future__ import annotations

import os

# backend.core.security 导入期校验 JWT_SECRET_KEY（root tests 无 conftest 注入）。
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-testing-32-bytes-ok")
os.environ.setdefault("DATABASE_URL", "sqlite:///./test-settings-auth.db")

import pytest

from backend.core.settings.security import (
    AuthSessionSettings,
    get_auth_session_settings,
    reset_auth_session_settings_cache,
)

_PRE_MIGRATION_DEFAULTS = {
    "auth_access_cookie_name": "stp_access_token",
    "auth_refresh_cookie_name": "stp_refresh_token",
    "auth_cookie_path": "/",
    "auth_cookie_secure": "0",
    "auth_cookie_samesite": "lax",
    "stp_allow_register": "",
    "stp_csrf_enabled": "1",
    "cors_origins": "http://localhost:5173,http://127.0.0.1:5173,http://localhost:3000",
    "cors_allow_methods": "GET,POST,PUT,PATCH,DELETE,OPTIONS",
    "cors_allow_headers": "Authorization,Content-Type,X-Agent-Secret",
}

_MIGRATED_ENVS = {
    "AUTH_ACCESS_COOKIE_NAME",
    "AUTH_REFRESH_COOKIE_NAME",
    "AUTH_COOKIE_PATH",
    "AUTH_COOKIE_SECURE",
    "AUTH_COOKIE_SAMESITE",
    "STP_ALLOW_REGISTER",
    "STP_CSRF_ENABLED",
    "CORS_ORIGINS",
    "CORS_ALLOW_METHODS",
    "CORS_ALLOW_HEADERS",
}


@pytest.fixture(autouse=True)
def _clean():
    for name in _MIGRATED_ENVS:
        os.environ.pop(name, None)
    reset_auth_session_settings_cache()
    yield
    reset_auth_session_settings_cache()


def test_defaults_match_pre_migration_values():
    settings = AuthSessionSettings()
    for name, expected in _PRE_MIGRATION_DEFAULTS.items():
        assert getattr(settings, name) == expected, name
        assert isinstance(getattr(settings, name), str), name


def test_derived_semantics_match_migration(monkeypatch):
    # AUTH_COOKIE_SECURE：仅 "1" 为开（其余一律关，迁移前语义）
    settings = AuthSessionSettings()
    assert settings.cookie_secure_enabled is False
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "1")
    reset_auth_session_settings_cache()
    assert get_auth_session_settings().cookie_secure_enabled is True
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "true")  # 迁移前同样判为关
    reset_auth_session_settings_cache()
    assert get_auth_session_settings().cookie_secure_enabled is False

    # SAMESITE：原始值保留、归一值非法回落 lax
    monkeypatch.setenv("AUTH_COOKIE_SAMESITE", "STRICT")
    reset_auth_session_settings_cache()
    s = get_auth_session_settings()
    assert s.cookie_samesite_raw == "strict"
    assert s.cookie_samesite_normalized == "strict"
    monkeypatch.setenv("AUTH_COOKIE_SAMESITE", "None")
    reset_auth_session_settings_cache()
    assert get_auth_session_settings().cookie_samesite_normalized == "none"
    monkeypatch.setenv("AUTH_COOKIE_SAMESITE", "bogus")
    reset_auth_session_settings_cache()
    s = get_auth_session_settings()
    assert s.cookie_samesite_raw == "bogus"          # guard 用它报错
    assert s.cookie_samesite_normalized == "lax"     # 读取方回落

    # CSRF：0/false/no/off 为关，未知值默认开（迁移前 guard 语义）
    monkeypatch.setenv("STP_CSRF_ENABLED", "off")
    reset_auth_session_settings_cache()
    assert get_auth_session_settings().csrf_enabled is False
    monkeypatch.setenv("STP_CSRF_ENABLED", "weird")
    reset_auth_session_settings_cache()
    assert get_auth_session_settings().csrf_enabled is True


def test_env_override_and_cache_reset(monkeypatch):
    assert get_auth_session_settings().auth_access_cookie_name == "stp_access_token"
    monkeypatch.setenv("AUTH_ACCESS_COOKIE_NAME", "custom_access")
    assert get_auth_session_settings().auth_access_cookie_name == "stp_access_token"  # 缓存
    reset_auth_session_settings_cache()
    assert get_auth_session_settings().auth_access_cookie_name == "custom_access"


def test_module_attributes_are_lazy(monkeypatch):
    """@security.ACCESS_COOKIE_NAME 等模块属性为惰性代理（PEP 562），非 import 固化。"""
    import backend.core.security as security

    assert security.ACCESS_COOKIE_NAME == "stp_access_token"
    monkeypatch.setenv("AUTH_ACCESS_COOKIE_NAME", "lazy_probe")
    assert security.ACCESS_COOKIE_NAME == "stp_access_token"  # 未清缓存仍是旧值
    reset_auth_session_settings_cache()
    assert security.ACCESS_COOKIE_NAME == "lazy_probe"
    with pytest.raises(AttributeError):
        security.NOT_A_REAL_ATTR  # noqa: B018


def test_dotenv_file_alone_must_not_take_effect(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("AUTH_ACCESS_COOKIE_NAME=from_file\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    reset_auth_session_settings_cache()
    assert get_auth_session_settings().auth_access_cookie_name == "stp_access_token"


def test_production_guard_behaves_as_before(monkeypatch):
    from backend.core.security import validate_production_auth_cookie_settings

    # 非生产环境：直接放行
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv("TESTING", "0")
    validate_production_auth_cookie_settings()

    # ENV=production 且未开 secure → RuntimeError（文案不变）
    monkeypatch.setenv("ENV", "production")
    reset_auth_session_settings_cache()
    with pytest.raises(RuntimeError, match="AUTH_COOKIE_SECURE=1"):
        validate_production_auth_cookie_settings()

    # secure=1 但 samesite 非法 → RuntimeError（校验原始值）
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "1")
    monkeypatch.setenv("AUTH_COOKIE_SAMESITE", "bogus")
    reset_auth_session_settings_cache()
    with pytest.raises(RuntimeError, match="AUTH_COOKIE_SAMESITE="):
        validate_production_auth_cookie_settings()

    # CSRF 显式关闭 → RuntimeError
    monkeypatch.setenv("AUTH_COOKIE_SAMESITE", "lax")
    monkeypatch.setenv("STP_CSRF_ENABLED", "0")
    reset_auth_session_settings_cache()
    with pytest.raises(RuntimeError, match="STP_CSRF_ENABLED"):
        validate_production_auth_cookie_settings()

    # 全部合规 → 放行
    monkeypatch.setenv("STP_CSRF_ENABLED", "1")
    reset_auth_session_settings_cache()
    validate_production_auth_cookie_settings()


def test_cors_guardrails_behave_as_before(monkeypatch):
    from backend.core.cors import get_cors_config

    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    reset_auth_session_settings_cache()
    cfg = get_cors_config()
    assert cfg["allow_origins"] == [
        "http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:3000",
    ]
    assert cfg["allow_credentials"] is True

    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:5173,*")
    reset_auth_session_settings_cache()
    with pytest.raises(RuntimeError, match="CORS_ORIGINS must not contain wildcard"):
        get_cors_config()

    monkeypatch.setenv("CORS_ORIGINS", " ")
    reset_auth_session_settings_cache()
    with pytest.raises(RuntimeError, match="at least one explicit origin"):
        get_cors_config()
