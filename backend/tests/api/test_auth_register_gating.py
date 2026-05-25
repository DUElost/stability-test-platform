"""Production / env gating for POST /auth/register."""

from __future__ import annotations

import pytest


def test_register_allowed_by_default_in_non_production(client, monkeypatch):
    monkeypatch.delenv("STP_ALLOW_REGISTER", raising=False)
    monkeypatch.setenv("ENV", "development")
    resp = client.post(
        "/api/v1/auth/register",
        json={"username": "newdev", "password": "secret123"},
    )
    assert resp.status_code == 200
    assert resp.json()["username"] == "newdev"


def test_register_blocked_in_production(client, monkeypatch):
    monkeypatch.delenv("STP_ALLOW_REGISTER", raising=False)
    monkeypatch.setenv("ENV", "production")
    resp = client.post(
        "/api/v1/auth/register",
        json={"username": "produser", "password": "secret123"},
    )
    assert resp.status_code == 403
    assert "disabled" in resp.json()["detail"].lower()


def test_register_explicitly_allowed_in_production(client, monkeypatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("STP_ALLOW_REGISTER", "1")
    resp = client.post(
        "/api/v1/auth/register",
        json={"username": "prodallowed", "password": "secret123"},
    )
    assert resp.status_code == 200


def test_register_blocked_when_env_flag_zero(client, monkeypatch):
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv("STP_ALLOW_REGISTER", "0")
    resp = client.post(
        "/api/v1/auth/register",
        json={"username": "blocked", "password": "secret123"},
    )
    assert resp.status_code == 403
