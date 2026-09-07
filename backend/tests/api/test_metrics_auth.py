"""Optional auth gate for /metrics when STP_METRICS_AUTH_REQUIRED=1."""

from __future__ import annotations

import pytest


@pytest.fixture
def metrics_auth_on(monkeypatch):
    monkeypatch.setenv("STP_METRICS_AUTH_REQUIRED", "1")


def test_metrics_public_when_auth_disabled(client, monkeypatch):
    monkeypatch.setenv("STP_METRICS_AUTH_REQUIRED", "0")
    resp = client.get("/metrics")
    assert resp.status_code == 200


def test_metrics_requires_auth_when_enabled(client, metrics_auth_on):
    resp = client.get("/metrics")
    assert resp.status_code == 401


def test_metrics_accepts_bearer_access_token(client, metrics_auth_on, auth_headers):
    resp = client.get("/metrics", headers=auth_headers)
    assert resp.status_code == 200


def test_metrics_accepts_agent_secret(client, metrics_auth_on, monkeypatch):
    monkeypatch.setenv("AGENT_SECRET", "metrics-test-secret")
    resp = client.get("/metrics", headers={"X-Agent-Secret": "metrics-test-secret"})
    assert resp.status_code == 200


def test_metrics_health_stays_public(client, metrics_auth_on):
    resp = client.get("/metrics/health")
    assert resp.status_code == 200


def test_metrics_rejects_refresh_token(client, metrics_auth_on):
    from backend.core.security import create_refresh_token

    refresh = create_refresh_token({"sub": "testuser"})
    resp = client.get("/metrics", headers={"Authorization": f"Bearer {refresh}"})
    assert resp.status_code == 401


# ── R02-D3（#903）：metrics Bearer 与 REST 同强度 ──────────────────────────


def _token_for(test_user) -> str:
    from backend.core.security import create_access_token

    return create_access_token(
        data={
            "sub": str(test_user.id),
            "username": test_user.username,
            "role": test_user.role,
            "ver": test_user.token_version,
        }
    )


def test_metrics_rejects_token_of_disabled_user(
    client, metrics_auth_on, test_user, db_session
):
    """#903 核心场景：签名有效的 token，停用用户后必须被拒。

    直改 DB 不经 API（不触发 ver bump），隔离验证 is_active 检查本身。"""
    headers = {"Authorization": f"Bearer {_token_for(test_user)}"}
    assert client.get("/metrics", headers=headers).status_code == 200

    test_user.is_active = "N"
    db_session.commit()

    resp = client.get("/metrics", headers=headers)
    assert resp.status_code == 401


def test_metrics_rejects_stale_epoch_token(
    client, metrics_auth_on, test_user, db_session
):
    """R02-D2：ver 纪元不匹配（bump 后旧 token）必须被拒。"""
    headers = {"Authorization": f"Bearer {_token_for(test_user)}"}
    assert client.get("/metrics", headers=headers).status_code == 200

    test_user.token_version = (test_user.token_version or 1) + 1
    db_session.commit()

    resp = client.get("/metrics", headers=headers)
    assert resp.status_code == 401
