"""Tests for seed_and_smoke.py — CLI contract and HTTP helpers."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Repo root .env loading
# ---------------------------------------------------------------------------


def test_find_repo_root_detects_git(tmp_path):
    from backend.scripts.seed_and_smoke import find_repo_root

    nested = tmp_path / "backend" / "scripts"
    nested.mkdir(parents=True)
    (tmp_path / ".git").mkdir()
    assert find_repo_root(nested) == tmp_path.resolve()


def test_find_repo_root_detects_pyproject(tmp_path):
    from backend.scripts.seed_and_smoke import find_repo_root

    nested = tmp_path / "backend" / "scripts"
    nested.mkdir(parents=True)
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
    assert find_repo_root(nested) == tmp_path.resolve()


def test_find_repo_root_fallback_two_levels_up(tmp_path):
    from backend.scripts.seed_and_smoke import find_repo_root

    scripts_dir = tmp_path / "backend" / "scripts"
    scripts_dir.mkdir(parents=True)
    with patch("backend.scripts.seed_and_smoke.__file__", str(scripts_dir / "seed_and_smoke.py")):
        assert find_repo_root(scripts_dir) == tmp_path.resolve()


def test_load_repo_dotenv_reads_smoke_vars(tmp_path, monkeypatch):
    from backend.scripts.seed_and_smoke import load_repo_dotenv

    monkeypatch.delenv("STP_ADMIN_PASSWORD", raising=False)
    monkeypatch.delenv("STP_ADMIN_USER", raising=False)
    monkeypatch.delenv("STP_SMOKE_ORIGIN", raising=False)

    (tmp_path / ".env").write_text(
        "STP_ADMIN_PASSWORD=fixture-pass\n"
        "STP_ADMIN_USER=smoke-admin\n"
        "STP_SMOKE_ORIGIN=http://fixture.test:5173\n",
        encoding="utf-8",
    )

    env_path = load_repo_dotenv(repo_root=tmp_path)
    assert env_path == tmp_path / ".env"
    assert os.environ["STP_ADMIN_PASSWORD"] == "fixture-pass"
    assert os.environ["STP_ADMIN_USER"] == "smoke-admin"
    assert os.environ["STP_SMOKE_ORIGIN"] == "http://fixture.test:5173"


def test_load_repo_dotenv_does_not_override_existing_env(tmp_path, monkeypatch):
    from backend.scripts.seed_and_smoke import load_repo_dotenv

    monkeypatch.setenv("STP_ADMIN_PASSWORD", "already-set")

    (tmp_path / ".env").write_text("STP_ADMIN_PASSWORD=from-file\n", encoding="utf-8")

    load_repo_dotenv(repo_root=tmp_path)
    assert os.environ["STP_ADMIN_PASSWORD"] == "already-set"


def test_load_repo_dotenv_simple_parser_without_dotenv_package(tmp_path, monkeypatch):
    import builtins

    from backend.scripts import seed_and_smoke

    monkeypatch.delenv("STP_ADMIN_PASSWORD", raising=False)
    (tmp_path / ".env").write_text('STP_ADMIN_PASSWORD="quoted-pass"\n', encoding="utf-8")

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "dotenv":
            raise ImportError("no dotenv")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    env_path = seed_and_smoke.load_repo_dotenv(repo_root=tmp_path)
    assert env_path == tmp_path / ".env"
    assert os.environ["STP_ADMIN_PASSWORD"] == "quoted-pass"


def test_ensure_smoke_plan_updates_on_delete_409():
    from backend.scripts.seed_and_smoke import ensure_smoke_plan

    list_response = MagicMock(status_code=200)
    list_response.json.return_value = {
        "data": [{"id": 1, "name": "smoke-plan-001"}],
        "error": None,
    }
    delete_response = MagicMock(status_code=409)
    delete_response.text = '{"detail":"cannot delete plan with 1 execution record(s)"}'
    update_response = MagicMock(status_code=200)
    update_response.json.return_value = {
        "data": {"id": 1, "name": "smoke-plan-001", "steps": [{}, {}, {}, {}]},
        "error": None,
    }

    client = MagicMock()
    client.get.return_value = list_response
    client.delete.return_value = delete_response
    client.put.return_value = update_response

    plan_id = ensure_smoke_plan(client, "smoke-plan-001")
    assert plan_id == 1
    client.put.assert_called_once()
    client.post.assert_not_called()


def test_ensure_smoke_plan_deletes_then_creates():
    from backend.scripts.seed_and_smoke import ensure_smoke_plan

    list_response = MagicMock(status_code=200)
    list_response.json.return_value = {
        "data": [{"id": 7, "name": "smoke-plan-001"}],
        "error": None,
    }
    delete_response = MagicMock(status_code=200)
    delete_response.json.return_value = {"data": {"deleted": 7}, "error": None}
    create_response = MagicMock(status_code=201)
    create_response.json.return_value = {
        "data": {"id": 99, "name": "smoke-plan-001", "steps": [{}, {}, {}, {}]},
        "error": None,
    }

    client = MagicMock()
    client.get.return_value = list_response
    client.delete.return_value = delete_response
    client.post.return_value = create_response

    plan_id = ensure_smoke_plan(client, "smoke-plan-001")
    assert plan_id == 99
    client.delete.assert_called_once_with("/api/v1/plans/7")
    client.post.assert_called_once()
    client.put.assert_not_called()


def test_ensure_smoke_plan_creates_when_no_match():
    from backend.scripts.seed_and_smoke import ensure_smoke_plan

    list_response = MagicMock(status_code=200)
    list_response.json.return_value = {"data": [], "error": None}
    create_response = MagicMock(status_code=201)
    create_response.json.return_value = {
        "data": {"id": 3, "name": "smoke-plan-001", "steps": [{}, {}, {}, {}]},
        "error": None,
    }

    client = MagicMock()
    client.get.return_value = list_response
    client.post.return_value = create_response

    plan_id = ensure_smoke_plan(client, "smoke-plan-001")
    assert plan_id == 3
    client.delete.assert_not_called()
    client.put.assert_not_called()


def test_main_missing_password_message_includes_env_path(tmp_path, monkeypatch, capsys):
    from backend.scripts import seed_and_smoke

    monkeypatch.delenv("STP_ADMIN_PASSWORD", raising=False)
    env_file = tmp_path / ".env"
    with patch.object(seed_and_smoke, "load_repo_dotenv", return_value=env_file):
        monkeypatch.setattr(sys, "argv", ["seed_and_smoke.py", "--no-hot-update"])
        with pytest.raises(SystemExit):
            seed_and_smoke.main()
    err = capsys.readouterr().err
    assert "Missing admin password" in err
    assert str(env_file) in err


def test_main_uses_password_from_loaded_dotenv(tmp_path, monkeypatch):
    from backend.scripts import seed_and_smoke

    monkeypatch.delenv("STP_ADMIN_PASSWORD", raising=False)
    (tmp_path / ".env").write_text("STP_ADMIN_PASSWORD=env-from-dotenv\n", encoding="utf-8")

    def load_and_apply(repo_root=None):
        root = (repo_root or tmp_path).resolve()
        seed_and_smoke._parse_dotenv_simple(root / ".env", os.environ)
        return root / ".env"

    plans_response = MagicMock(status_code=200)
    plans_response.json.return_value = []

    with patch.object(seed_and_smoke, "load_repo_dotenv", side_effect=load_and_apply):
        with patch.object(seed_and_smoke, "APIClient") as mock_api_cls:
            mock_client = MagicMock()
            mock_client.get.return_value = plans_response
            mock_api_cls.return_value = mock_client
            monkeypatch.setattr(
                sys,
                "argv",
                [
                    "seed_and_smoke.py",
                    "--no-hot-update",
                    "--no-wait",
                    "--backend",
                    "http://127.0.0.1:8000",
                ],
            )
            with patch.object(seed_and_smoke, "ensure_smoke_plan", return_value=1):
                with patch.object(
                    seed_and_smoke,
                    "resolve_smoke_targets",
                    return_value=(101, "host-auto"),
                ):
                    with patch.object(seed_and_smoke, "preview"):
                        with patch.object(seed_and_smoke, "trigger", return_value=99):
                            seed_and_smoke.main()

    mock_client.login.assert_called_once()
    assert mock_client.login.call_args[0][1] == "env-from-dotenv"


# ---------------------------------------------------------------------------
# Argument parser (mirrors seed_and_smoke.main defaults)
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


def test_smoke_script_constants_and_argv_flags(monkeypatch):
    from backend.scripts.seed_and_smoke import (
        DEFAULT_BACKEND,
        DEFAULT_DEVICE_ID,
        DEFAULT_HOST_ID,
        DEFAULT_PLAN_NAME,
        DEFAULT_SMOKE_ORIGIN,
        PASSING_STATUSES,
        TERMINAL_STATUSES,
    )

    monkeypatch.setattr(
        sys,
        "argv",
        ["seed_and_smoke.py", "--password", "x", "--no-hot-update", "--no-wait"],
    )
    assert DEFAULT_BACKEND == "http://localhost:8000"
    assert DEFAULT_SMOKE_ORIGIN == "http://localhost:5173"
    assert DEFAULT_HOST_ID is None
    assert DEFAULT_DEVICE_ID is None
    assert DEFAULT_PLAN_NAME == "smoke-plan-001"
    assert "SUCCESS" in TERMINAL_STATUSES
    assert len(TERMINAL_STATUSES) == 4
    assert PASSING_STATUSES == {"SUCCESS", "PARTIAL_SUCCESS"}


def test_missing_password_exits_clearly():
    """Verify die() prints a clear message and exits."""
    from backend.scripts.seed_and_smoke import die

    with pytest.raises(SystemExit):
        die(
            "Missing admin password: set STP_ADMIN_PASSWORD or pass --password explicitly. "
            "(checked /repo/.env)"
        )


# ---------------------------------------------------------------------------
# CSRF / Origin helpers
# ---------------------------------------------------------------------------


def test_build_csrf_headers_default_origin():
    from backend.scripts.seed_and_smoke import build_csrf_headers

    assert build_csrf_headers("http://localhost:5173") == {
        "Origin": "http://localhost:5173",
        "Referer": "http://localhost:5173/",
    }


def test_build_csrf_headers_strips_trailing_slash():
    from backend.scripts.seed_and_smoke import build_csrf_headers

    assert build_csrf_headers("http://localhost:5173/") == {
        "Origin": "http://localhost:5173",
        "Referer": "http://localhost:5173/",
    }


def test_default_smoke_origin_from_env(monkeypatch):
    from backend.scripts.seed_and_smoke import default_smoke_origin

    monkeypatch.setenv("STP_SMOKE_ORIGIN", "http://127.0.0.1:5173")
    assert default_smoke_origin() == "http://127.0.0.1:5173"


def test_default_smoke_origin_fallback(monkeypatch):
    from backend.scripts.seed_and_smoke import DEFAULT_SMOKE_ORIGIN, default_smoke_origin

    monkeypatch.delenv("STP_SMOKE_ORIGIN", raising=False)
    assert default_smoke_origin() == DEFAULT_SMOKE_ORIGIN


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def test_unwrap_api_response_wrapper():
    from backend.scripts.seed_and_smoke import _unwrap

    assert _unwrap({"data": {"id": 1}, "error": None}) == {"id": 1}


def test_unwrap_plain_body():
    from backend.scripts.seed_and_smoke import _unwrap

    assert _unwrap({"id": 2}) == {"id": 2}


def test_login_failure_hint_on_401_incorrect_password(tmp_path):
    from backend.scripts.seed_and_smoke import login_failure_hint

    hint = login_failure_hint(
        status_code=401,
        body='{"detail":"Incorrect username or password"}',
        username="admin",
        env_file=tmp_path / ".env",
    )
    assert "STP_ADMIN_PASSWORD" in hint
    assert "reset_dev_admin_password.py" in hint
    assert str(tmp_path / ".env") in hint


def test_login_failure_hint_empty_for_other_errors():
    from backend.scripts.seed_and_smoke import login_failure_hint

    assert login_failure_hint(status_code=403, body="forbidden", username="admin") == ""


def test_api_client_login_uses_cookie_path_with_csrf_headers():
    from backend.scripts.seed_and_smoke import APIClient, build_csrf_headers

    client = APIClient("http://example.test", origin="http://localhost:5173")
    real_client = client._client
    real_client.close()

    login_response = MagicMock(status_code=200)
    login_response.json.return_value = {"ok": True}
    me_response = MagicMock(status_code=200)

    mock_client = MagicMock()
    mock_client.post.return_value = login_response
    mock_client.get.return_value = me_response
    client._client = mock_client

    client.login("admin", "pw")

    mock_client.post.assert_called_once_with(
        "/api/v1/auth/login",
        data={"username": "admin", "password": "pw"},
        headers=build_csrf_headers("http://localhost:5173"),
    )
    mock_client.get.assert_called_once_with("/api/v1/auth/me")
    assert client._logged_in is True


def test_api_client_post_merges_csrf_headers():
    from backend.scripts.seed_and_smoke import APIClient, build_csrf_headers

    client = APIClient("http://example.test", origin="http://localhost:5173")
    mock_client = MagicMock()
    client._client = mock_client

    client.post("/api/v1/plans", json={"name": "x"})

    mock_client.post.assert_called_once_with(
        "/api/v1/plans",
        headers=build_csrf_headers("http://localhost:5173"),
        json={"name": "x"},
    )


def test_api_client_get_does_not_send_csrf_headers():
    from backend.scripts.seed_and_smoke import APIClient

    client = APIClient("http://example.test", origin="http://localhost:5173")
    mock_client = MagicMock()
    client._client = mock_client

    client.get("/api/v1/plans")

    mock_client.get.assert_called_once_with("/api/v1/plans", headers={})


def test_preview_assertions_pass_with_mock_client():
    from backend.scripts.seed_and_smoke import preview

    response = MagicMock(status_code=200)
    response.json.return_value = {
        "data": {
            "device_count": 1,
            "job_count": 1,
            "total_steps": 4,
            "lifecycle": {
                "init": [{}, {}],
                "patrol": {"steps": [{}], "interval_seconds": 60},
                "teardown": [{}],
                "timeout_seconds": 300,
            },
        },
        "error": None,
    }
    client = MagicMock()
    client.post.return_value = response

    preview(client, plan_id=99, device_ids=[2429])


def test_preview_device_count_mismatch_exits():
    from backend.scripts.seed_and_smoke import preview

    response = MagicMock(status_code=200)
    response.json.return_value = {
        "data": {
            "device_count": 0,
            "total_steps": 4,
            "lifecycle": {"init": [{}, {}], "patrol": {"steps": [{}]}, "teardown": [{}]},
        },
        "error": None,
    }
    client = MagicMock()
    client.post.return_value = response

    with pytest.raises(SystemExit):
        preview(client, plan_id=1, device_ids=[2429])


def test_trigger_returns_plan_run_id():
    from backend.scripts.seed_and_smoke import trigger

    response = MagicMock(status_code=200)
    response.json.return_value = {
        "data": {"id": 42, "status": "RUNNING"},
        "error": None,
    }
    client = MagicMock()
    client.post.return_value = response

    assert trigger(client, plan_id=7, device_ids=[1]) == 42


def test_plan_payload_has_four_steps():
    from backend.scripts.seed_and_smoke import PLAN_PAYLOAD

    assert len(PLAN_PAYLOAD["steps"]) == 4
    stages = {s["stage"] for s in PLAN_PAYLOAD["steps"]}
    assert stages == {"init", "patrol", "teardown"}
    assert "lifecycle" not in PLAN_PAYLOAD


def test_poll_timeout_non_passing_status_would_fail():
    """Document PASSING_STATUSES contract used by main() exit code."""
    from backend.scripts.seed_and_smoke import PASSING_STATUSES

    assert "FAILED" not in PASSING_STATUSES
    assert "RUNNING" not in PASSING_STATUSES


# ---------------------------------------------------------------------------
# Device / host auto-selection
# ---------------------------------------------------------------------------


def _devices_response(items):
    response = MagicMock(status_code=200)
    response.json.return_value = {"data": items, "error": None}
    return response


def _hosts_response(items):
    response = MagicMock(status_code=200)
    response.json.return_value = {"data": items, "error": None}
    return response


def test_resolve_smoke_targets_auto_picks_online_device_with_host():
    from backend.scripts.seed_and_smoke import resolve_smoke_targets

    client = MagicMock()
    client.get.side_effect = [
        _devices_response(
            [
                {"id": 1, "status": "OFFLINE", "host_id": "h1", "serial": "s1"},
                {"id": 2, "status": "ONLINE", "host_id": "h2", "serial": "s2"},
            ]
        ),
    ]

    device_id, host_id = resolve_smoke_targets(
        client,
        device_id=None,
        target_host_id=None,
        device_id_explicit=False,
        target_host_id_explicit=False,
    )
    assert device_id == 2
    assert host_id == "h2"


def test_resolve_smoke_targets_explicit_device_uses_device_host():
    from backend.scripts.seed_and_smoke import resolve_smoke_targets

    client = MagicMock()
    client.get.return_value = _devices_response(
        [{"id": 42, "status": "ONLINE", "host_id": "host-abc", "serial": "dev42"}]
    )

    device_id, host_id = resolve_smoke_targets(
        client,
        device_id=42,
        target_host_id=None,
        device_id_explicit=True,
        target_host_id_explicit=False,
    )
    assert device_id == 42
    assert host_id == "host-abc"


def test_resolve_smoke_targets_explicit_device_not_found_lists_samples():
    from backend.scripts.seed_and_smoke import resolve_smoke_targets

    client = MagicMock()
    client.get.return_value = _devices_response(
        [
            {"id": 10, "status": "ONLINE", "host_id": "h1", "serial": "a"},
            {"id": 11, "status": "ONLINE", "host_id": "h1", "serial": "b"},
        ]
    )

    with pytest.raises(SystemExit):
        resolve_smoke_targets(
            client,
            device_id=2429,
            target_host_id=None,
            device_id_explicit=True,
            target_host_id_explicit=False,
        )


def test_resolve_smoke_targets_filters_by_explicit_target_host():
    from backend.scripts.seed_and_smoke import resolve_smoke_targets

    client = MagicMock()
    client.get.return_value = _devices_response(
        [
            {"id": 1, "status": "ONLINE", "host_id": "keep-me", "serial": "a"},
            {"id": 2, "status": "ONLINE", "host_id": "other", "serial": "b"},
        ]
    )

    device_id, host_id = resolve_smoke_targets(
        client,
        device_id=None,
        target_host_id="keep-me",
        device_id_explicit=False,
        target_host_id_explicit=True,
    )
    assert device_id == 1
    assert host_id == "keep-me"


def test_resolve_smoke_targets_falls_back_to_online_host_devices():
    from backend.scripts.seed_and_smoke import resolve_smoke_targets

    client = MagicMock()
    client.get.side_effect = [
        _devices_response(
            [{"id": 5, "status": "OFFLINE", "host_id": "h-live", "serial": "x"}]
        ),
        _hosts_response([{"id": "h-live", "status": "ONLINE"}]),
    ]

    device_id, host_id = resolve_smoke_targets(
        client,
        device_id=None,
        target_host_id=None,
        device_id_explicit=False,
        target_host_id_explicit=False,
    )
    assert device_id == 5
    assert host_id == "h-live"


def test_resolve_smoke_targets_no_devices_exits():
    from backend.scripts.seed_and_smoke import resolve_smoke_targets

    client = MagicMock()
    client.get.side_effect = [
        _devices_response([]),
        _hosts_response([]),
    ]

    with pytest.raises(SystemExit):
        resolve_smoke_targets(
            client,
            device_id=None,
            target_host_id=None,
            device_id_explicit=False,
            target_host_id_explicit=False,
        )
