from __future__ import annotations

import pytest
from fastapi import HTTPException

from backend.api.routes.agent_api import _verify_agent
from backend.api.routes.auth import verify_agent_secret
from backend.main import fastapi_app, lifespan


def test_verify_agent_secret_rejects_missing_server_secret(monkeypatch):
    monkeypatch.setenv("TESTING", "0")
    monkeypatch.delenv("AGENT_SECRET", raising=False)

    with pytest.raises(HTTPException) as excinfo:
        verify_agent_secret("anything")

    assert excinfo.value.status_code == 503
    assert "AGENT_SECRET not configured" in str(excinfo.value.detail)


def test_agent_api_verify_rejects_placeholder_server_secret(monkeypatch):
    monkeypatch.setenv("TESTING", "0")
    monkeypatch.setenv("AGENT_SECRET", "change-me-in-production")

    with pytest.raises(HTTPException) as excinfo:
        _verify_agent("change-me-in-production")

    assert excinfo.value.status_code == 503
    assert "AGENT_SECRET not configured" in str(excinfo.value.detail)


@pytest.mark.asyncio
async def test_lifespan_requires_agent_secret_outside_testing(monkeypatch):
    monkeypatch.setenv("TESTING", "0")
    monkeypatch.setenv("ENV", "development")
    monkeypatch.delenv("AGENT_SECRET", raising=False)

    with pytest.raises(RuntimeError, match="AGENT_SECRET required"):
        async with lifespan(fastapi_app):
            pass


@pytest.mark.asyncio
async def test_lifespan_requires_secure_auth_cookies_in_production(monkeypatch):
    monkeypatch.setenv("TESTING", "0")
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("AGENT_SECRET", "test-agent-secret")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "0")
    monkeypatch.setenv("AUTH_COOKIE_SAMESITE", "lax")

    with pytest.raises(RuntimeError, match="AUTH_COOKIE_SECURE=1"):
        async with lifespan(fastapi_app):
            pass


def test_internal_allows_http_cookies_without_secure(monkeypatch):
    """#281 部署决策(操作者选定):ENV=internal 是「无 TLS 内网部署」标识——
    AUTH_COOKIE_SECURE=0 合法(Secure cookie 在纯 HTTP 下被浏览器拒绝,
    强制它=必然拒启且无安全收益);其余生产级护栏仍全部强制。"""
    from backend.core.security import validate_production_auth_cookie_settings

    monkeypatch.setenv("TESTING", "0")
    monkeypatch.setenv("ENV", "internal")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "0")
    monkeypatch.setenv("AUTH_COOKIE_SAMESITE", "lax")
    monkeypatch.setenv("STP_CSRF_ENABLED", "1")

    validate_production_auth_cookie_settings()  # 不抛


def test_internal_still_rejects_csrf_disabled(monkeypatch):
    """internal 保留 CSRF 强制(#281):无 TLS 内网下关闭 CSRF 同样致命。"""
    from backend.core.security import validate_production_auth_cookie_settings

    monkeypatch.setenv("TESTING", "0")
    monkeypatch.setenv("ENV", "internal")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "0")
    monkeypatch.setenv("AUTH_COOKIE_SAMESITE", "lax")
    monkeypatch.setenv("STP_CSRF_ENABLED", "0")

    with pytest.raises(RuntimeError, match="STP_CSRF_ENABLED"):
        validate_production_auth_cookie_settings()


def test_internal_still_rejects_invalid_samesite(monkeypatch):
    """internal 保留 SameSite 取值校验(#281 CR Minor)。"""
    from backend.core.security import validate_production_auth_cookie_settings

    monkeypatch.setenv("TESTING", "0")
    monkeypatch.setenv("ENV", "internal")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "0")
    monkeypatch.setenv("AUTH_COOKIE_SAMESITE", "invalid")
    monkeypatch.setenv("STP_CSRF_ENABLED", "1")

    with pytest.raises(RuntimeError, match="AUTH_COOKIE_SAMESITE"):
        validate_production_auth_cookie_settings()


@pytest.mark.asyncio
async def test_lifespan_rejects_samesite_none_without_csrf_protection(monkeypatch):
    monkeypatch.setenv("TESTING", "0")
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("AGENT_SECRET", "test-agent-secret")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "1")
    monkeypatch.setenv("AUTH_COOKIE_SAMESITE", "none")

    with pytest.raises(RuntimeError, match="AUTH_COOKIE_SAMESITE=none"):
        async with lifespan(fastapi_app):
            pass


@pytest.mark.asyncio
async def test_lifespan_rejects_csrf_disabled_in_production(monkeypatch):
    monkeypatch.setenv("TESTING", "0")
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("AGENT_SECRET", "test-agent-secret")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "1")
    monkeypatch.setenv("AUTH_COOKIE_SAMESITE", "lax")
    monkeypatch.setenv("STP_CSRF_ENABLED", "0")

    with pytest.raises(RuntimeError, match="STP_CSRF_ENABLED"):
        async with lifespan(fastapi_app):
            pass


@pytest.mark.asyncio
async def test_lifespan_rejects_invalid_samesite_in_production(monkeypatch):
    """#281 CR Minor:无效 AUTH_COOKIE_SAMESITE 显式值必须使生产类环境
    启动失败,不得被 _get_cookie_samesite 静默回落为 lax。"""
    monkeypatch.setenv("TESTING", "0")
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("AGENT_SECRET", "test-agent-secret")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "1")
    monkeypatch.setenv("AUTH_COOKIE_SAMESITE", "invalid")

    with pytest.raises(RuntimeError, match="AUTH_COOKIE_SAMESITE"):
        async with lifespan(fastapi_app):
            pass
