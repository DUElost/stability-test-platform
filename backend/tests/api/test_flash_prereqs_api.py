"""主机页刷机前置归位 API（ensure_flash_prereqs）。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch


def _audit(db_session, action: str, host_id: str, details: dict, *, at: datetime):
    from backend.models.audit import AuditLog

    row = AuditLog(
        action=action,
        resource_type="host",
        resource_id=host_id,
        details=details,
        timestamp=at,
    )
    db_session.add(row)
    db_session.commit()
    return row


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
    """无活动运行且从无审计史 → 真 idle（#3180 后有审计史时不再回 idle）。"""
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


def test_flash_prereqs_status_replays_success_after_registry_cleared(
    client, auth_headers, sample_host, db_session
):
    """#3180：run 结束即清活动登记——终态必须从审计回放。

    回放缺位时成功的补齐也只能被前端轮询成 idle 直至 900s TIMEOUT（现场：
    48 台批量全部假失败，审计与 console 日志均为 SUCCESS）。
    """
    now = datetime.now(timezone.utc)
    _audit(
        db_session,
        "ensure_flash_prereqs_request",
        sample_host.id,
        {"console_run_id": "con-done"},
        at=now - timedelta(minutes=2),
    )
    _audit(
        db_session,
        "ensure_flash_prereqs",
        sample_host.id,
        {
            "ok": True,
            "rc": 0,
            "console_status": "SUCCESS",
            "console_run_id": "con-done",
            "log_path": "logs/console/con-done.log",
        },
        at=now - timedelta(minutes=1),
    )
    with patch(
        "backend.api.routes.hosts.get_active_flash_prereqs_console_id",
        return_value=None,
    ):
        resp = client.get(
            f"/api/v1/hosts/{sample_host.id}/flash-prereqs/status",
            headers=auth_headers,
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "succeeded"
    assert body["console_status"] == "SUCCESS"
    assert body["console_found"] is False
    assert body["console_run_id"] == "con-done"
    assert body["exit_code"] == 0
    assert body["log_path"] == "logs/console/con-done.log"


def test_flash_prereqs_status_replays_failed_with_rc(
    client, auth_headers, sample_host, db_session
):
    """失败回放：console_status/exit_code 以审计为准，前端立即收敛为 FAILED。"""
    now = datetime.now(timezone.utc)
    _audit(
        db_session,
        "ensure_flash_prereqs_request",
        sample_host.id,
        {"console_run_id": "con-bad"},
        at=now - timedelta(minutes=2),
    )
    _audit(
        db_session,
        "ensure_flash_prereqs",
        sample_host.id,
        {"ok": False, "rc": 2, "console_status": "FAILED", "console_run_id": "con-bad"},
        at=now - timedelta(minutes=1),
    )
    with patch(
        "backend.api.routes.hosts.get_active_flash_prereqs_console_id",
        return_value=None,
    ):
        resp = client.get(
            f"/api/v1/hosts/{sample_host.id}/flash-prereqs/status",
            headers=auth_headers,
        )
    body = resp.json()
    assert body["status"] == "failed"
    assert body["console_status"] == "FAILED"
    assert body["exit_code"] == 2
    # outcome 审计没带 log_path 时回退到 run 的落盘路径
    assert body["log_path"] and body["log_path"].endswith("con-bad.log")


def test_flash_prereqs_status_lost_when_outcome_stale(
    client, auth_headers, sample_host, db_session
):
    """新请求没有更新的结果 → lost（控制面重启/结果未及落库），不能报成 idle。"""
    now = datetime.now(timezone.utc)
    _audit(
        db_session,
        "ensure_flash_prereqs",
        sample_host.id,
        {"ok": True, "console_status": "SUCCESS", "console_run_id": "con-old"},
        at=now - timedelta(hours=2),
    )
    _audit(
        db_session,
        "ensure_flash_prereqs_request",
        sample_host.id,
        {"console_run_id": "con-lost"},
        at=now - timedelta(minutes=5),
    )
    with patch(
        "backend.api.routes.hosts.get_active_flash_prereqs_console_id",
        return_value=None,
    ):
        resp = client.get(
            f"/api/v1/hosts/{sample_host.id}/flash-prereqs/status",
            headers=auth_headers,
        )
    body = resp.json()
    assert body["status"] == "lost"
    assert body["console_status"] is None
    assert body["console_run_id"] == "con-lost"
