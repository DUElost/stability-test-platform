"""主机页刷机前置归位 API（ensure_flash_prereqs）。"""

from __future__ import annotations

from unittest.mock import patch


def test_ensure_flash_prereqs_requires_admin(client, auth_headers, sample_host):
    resp = client.post(
        f"/api/v1/hosts/{sample_host.id}/flash-prereqs/ensure",
        headers=auth_headers,
    )
    assert resp.status_code == 403


def test_ensure_flash_prereqs_starts_console(client, admin_headers, sample_host):
    with patch(
        "backend.api.routes.hosts.start_ensure_flash_prereqs_runconsole",
        return_value={
            "ok": True,
            "console_run_id": "run-flash-1",
            "room": "console:run-flash-1",
            "message": "ok",
        },
    ), patch("shutil.which", return_value="/usr/bin/ansible-playbook"):
        resp = client.post(
            f"/api/v1/hosts/{sample_host.id}/flash-prereqs/ensure",
            headers=admin_headers,
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["console_run_id"] == "run-flash-1"
    assert body["room"] == "console:run-flash-1"


def test_ensure_flash_prereqs_conflict_when_busy(client, admin_headers, sample_host):
    with patch(
        "backend.api.routes.hosts.start_ensure_flash_prereqs_runconsole",
        return_value={
            "ok": False,
            "message": "flash prereqs already in progress",
            "console_run_id": "run-busy",
        },
    ), patch("shutil.which", return_value="/usr/bin/ansible-playbook"):
        resp = client.post(
            f"/api/v1/hosts/{sample_host.id}/flash-prereqs/ensure",
            headers=admin_headers,
        )
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["console_run_id"] == "run-busy"


def test_flash_prereqs_status_idle(client, auth_headers, sample_host):
    with patch(
        "backend.api.routes.hosts.get_active_flash_prereqs_console_id",
        return_value=None,
    ):
        resp = client.get(
            f"/api/v1/hosts/{sample_host.id}/flash-prereqs/status",
            headers=auth_headers,
        )
    assert resp.status_code == 200
    assert resp.json()["status"] == "idle"
