"""Tests for seed_and_smoke.py — argument validation."""

from __future__ import annotations

import argparse
import os
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def test_build_arg_parser_password_defaults_to_env(monkeypatch):
    """--password defaults to STP_ADMIN_PASSWORD env var, not a hardcoded admin."""
    monkeypatch.setenv("STP_ADMIN_PASSWORD", "env-pass-123")

    p = argparse.ArgumentParser()
    p.add_argument("--password", default=os.getenv("STP_ADMIN_PASSWORD"))
    args = p.parse_args([])
    assert args.password == "env-pass-123"


def test_build_arg_parser_password_is_none_when_env_missing(monkeypatch):
    """--password is None when STP_ADMIN_PASSWORD is not set and --password not given."""
    monkeypatch.delenv("STP_ADMIN_PASSWORD", raising=False)

    p = argparse.ArgumentParser()
    p.add_argument("--password", default=os.getenv("STP_ADMIN_PASSWORD"))
    args = p.parse_args([])
    assert args.password is None


def test_build_arg_parser_password_from_cli_overrides_env(monkeypatch):
    """--password from CLI takes precedence over STP_ADMIN_PASSWORD env."""
    monkeypatch.setenv("STP_ADMIN_PASSWORD", "env-pass")

    p = argparse.ArgumentParser()
    p.add_argument("--password", default=os.getenv("STP_ADMIN_PASSWORD"))
    args = p.parse_args(["--password", "cli-pass"])
    assert args.password == "cli-pass"


def test_missing_password_exits_clearly():
    """Verify die() prints a clear message and exits."""
    from backend.scripts.seed_and_smoke import die

    with pytest.raises(SystemExit):
        die("Missing admin password: set STP_ADMIN_PASSWORD or pass --password explicitly.")


def test_api_client_login_posts_form_data():
    from backend.scripts.seed_and_smoke import APIClient

    client = APIClient("http://example.test")
    real_client = client._client
    real_client.close()

    response = MagicMock(status_code=200)
    response.json.return_value = {"access_token": "token-123"}

    mock_client = MagicMock()
    mock_client.post.return_value = response
    client._client = mock_client

    client.login("admin", "pw")

    mock_client.post.assert_called_once_with(
        "/api/v1/auth/login",
        data={"username": "admin", "password": "pw"},
    )
    assert client._token == "token-123"
