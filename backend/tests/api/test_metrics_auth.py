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
