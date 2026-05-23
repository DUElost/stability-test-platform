"""Refresh token blacklist tests.

Cover: revoke→refresh 拒绝;旧无 jti token grace 放行;logout 幂等;cleanup_expired。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import jwt
import pytest

from backend.core.security import (
    ACCESS_COOKIE_NAME,
    ALGORITHM,
    REFRESH_COOKIE_NAME,
    REFRESH_TOKEN_EXPIRE_DAYS,
    SECRET_KEY,
    create_access_token,
    create_refresh_token,
    decode_token,
)
from backend.models.token_blacklist import RevokedRefreshToken
from backend.services.token_blacklist import cleanup_expired, is_revoked, revoke


def _legacy_refresh_token_without_jti(username: str = "testuser") -> str:
    """Mimic a refresh token issued before the jti rollout (grace window input)."""
    expire = datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    payload = {"sub": username, "exp": expire, "type": "refresh"}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def test_refresh_token_carries_jti():
    token = create_refresh_token({"sub": "alice"})
    decoded = decode_token(token)
    assert decoded is not None
    assert decoded["type"] == "refresh"
    jti = decoded.get("jti")
    assert jti and isinstance(jti, str) and len(jti) >= 16


def test_revoke_is_idempotent(db_session):
    expires_at = datetime.now(timezone.utc) + timedelta(days=1)
    first = revoke(db_session, jti="jti-xyz", expires_at=expires_at, reason="logout")
    second = revoke(db_session, jti="jti-xyz", expires_at=expires_at, reason="logout")
    assert first is True
    assert second is False
    assert is_revoked(db_session, "jti-xyz") is True


def test_is_revoked_false_for_unknown_jti(db_session):
    assert is_revoked(db_session, "never-issued") is False
    assert is_revoked(db_session, "") is False


def test_cleanup_expired_removes_only_past_rows(db_session):
    now = datetime.now(timezone.utc)
    revoke(db_session, jti="old-1", expires_at=now - timedelta(seconds=1), reason="logout")
    revoke(db_session, jti="old-2", expires_at=now - timedelta(days=1), reason="logout")
    revoke(db_session, jti="future-1", expires_at=now + timedelta(days=10), reason="logout")

    deleted = cleanup_expired(db_session, now=now)
    assert deleted == 2

    remaining = {row.jti for row in db_session.query(RevokedRefreshToken).all()}
    assert remaining == {"future-1"}


def test_logout_blacklists_refresh_and_subsequent_refresh_rejected(client, test_user):
    login = client.post(
        "/api/v1/auth/login",
        data={"username": "testuser", "password": "testpass123"},
    )
    assert login.status_code == 200

    logout = client.post("/api/v1/auth/logout")
    assert logout.status_code == 200

    # 重放 logout 之前的 refresh cookie 必须被拒绝
    leaked_refresh = login.cookies.get(REFRESH_COOKIE_NAME)
    assert leaked_refresh

    client.cookies.clear()
    client.cookies.set(REFRESH_COOKIE_NAME, leaked_refresh)
    response = client.post("/api/v1/auth/refresh")
    assert response.status_code == 401


def test_logout_is_idempotent(client, test_user):
    login = client.post(
        "/api/v1/auth/login",
        data={"username": "testuser", "password": "testpass123"},
    )
    assert login.status_code == 200

    first = client.post("/api/v1/auth/logout")
    second = client.post("/api/v1/auth/logout")
    assert first.status_code == 200
    assert second.status_code == 200


def test_logout_without_refresh_token_returns_200(client):
    # 没有 cookie / 没有 body 的探测请求不应该暴露内部状态
    response = client.post("/api/v1/auth/logout")
    assert response.status_code == 200


def test_refresh_with_legacy_token_without_jti_passes_in_grace(client, test_user, caplog):
    legacy = _legacy_refresh_token_without_jti("testuser")
    client.cookies.set(REFRESH_COOKIE_NAME, legacy)

    with caplog.at_level(logging.WARNING, logger="backend.api.routes.auth"):
        response = client.post("/api/v1/auth/refresh")

    assert response.status_code == 200
    assert any("refresh_token_missing_jti" in r.message for r in caplog.records)


def test_refresh_with_jti_after_logout_rejected_via_body(client, test_user):
    """Bearer 模式的 refresh:通过 body 提交,同样应被黑名单拒绝。"""
    token_resp = client.post(
        "/api/v1/auth/token",
        data={"username": "testuser", "password": "testpass123"},
    )
    assert token_resp.status_code == 200
    refresh_token = token_resp.json()["refresh_token"]

    # 用 body 触发 logout 把这条 refresh 拉黑
    logout = client.post("/api/v1/auth/logout", json={"refresh_token": refresh_token})
    assert logout.status_code == 200

    # 再次拿同一 refresh 换 access 必须 401
    retry = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert retry.status_code == 401


# ── ADR-0024 P0: /auth/refresh 必须拒绝 access token 冒充 refresh ──────────


def test_refresh_endpoint_rejects_access_token_via_body(client, test_user):
    """access token 不能用作 refresh,防止 type 混淆带来的旁路。"""
    access = create_access_token({"sub": "testuser", "role": "user"})
    response = client.post("/api/v1/auth/refresh", json={"refresh_token": access})
    assert response.status_code == 401


def test_refresh_endpoint_rejects_access_token_via_cookie(client, test_user):
    access = create_access_token({"sub": "testuser", "role": "user"})
    client.cookies.set(REFRESH_COOKIE_NAME, access)
    response = client.post("/api/v1/auth/refresh")
    assert response.status_code == 401
