"""#940 — POST /api/v1/agent/logs host_id 字符串契约。

Host.id 是 String(64) PK；AgentLogQuery/AgentLogOut 曾用 int——非数字主机 ID
在请求校验阶段就被 422 拒绝，无法进入查询。契约：字符串 host_id 可查询，
输出原样回显。
"""

from __future__ import annotations

from types import SimpleNamespace


def test_nonnumeric_host_id_is_404_not_422(client):
    """验收：字符串 host_id（如 "host-a1"）通过校验进入 route；host 不存在
    → 404。旧 int schema 在校验期即 422。"""
    resp = client.post(
        "/api/v1/agent/logs",
        json={"host_id": "host-a1", "log_path": "/tmp/agent.log", "lines": 10},
    )
    assert resp.status_code == 404, resp.text


def test_missing_numeric_string_host_is_404(client):
    """数字形态的字符串也不存在时同样 404（不再有 int 特判）。"""
    resp = client.post(
        "/api/v1/agent/logs",
        json={"host_id": "999999", "log_path": "/tmp/agent.log", "lines": 10},
    )
    assert resp.status_code == 404, resp.text


def test_existing_host_string_id_roundtrips(client, sample_host, monkeypatch):
    """验收：存在的主机用字符串 id 查询 → 200，host_id 原样回显。"""
    creds = SimpleNamespace(
        password=None, key_path=None, user="root", known_hosts_path=None,
    )
    monkeypatch.setattr(
        "backend.api.routes.logs.resolve_host_ssh_credentials",
        lambda host, inventory_lookup=None: (creds, False),
    )

    resp = client.post(
        "/api/v1/agent/logs",
        json={
            "host_id": sample_host.id,
            "log_path": "/opt/stability-test-agent/logs/agent.log",
            "lines": 10,
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["host_id"] == sample_host.id
    assert data["error"] == "SSH credentials are not configured for this host."


# ── #1805 矩阵 row 12：退役机的日志尾读**允许但需审计**（ADR-0038 D-5 回收类）──


def _ssh_creds(monkeypatch):
    monkeypatch.setattr(
        "backend.api.routes.logs.resolve_host_ssh_credentials",
        lambda host, inventory_lookup=None: (
            SimpleNamespace(password=None, key_path=None, user="root", known_hosts_path=None),
            False,
        ),
    )


def _audits(db, host_id):
    from backend.models.audit import AuditLog

    return (
        db.query(AuditLog)
        .filter(AuditLog.action == "host_retired_log_tail")
        .filter(AuditLog.resource_id == host_id)
        .all()
    )


def test_retired_host_log_tail_is_allowed_and_audited(
    client, sample_host, db_session, engine, monkeypatch,
):
    """退役机的尾读**允许**（回收类，不拒绝）+ **落审计且已提交**（ADR-0038 D-5）。

    #2047：断言必须用**独立连接**读同一张表。本端点走 `get_db()`，请求结束只
    `close()`——只 flush 未 commit 的行会在请求结束时被回滚，而同一 `db_session`
    能看到它（conftest 的 client 把 `get_db` 覆盖成同一个 session）。即：同 session
    的断言无法区分「flush 过」与「已提交」，本用例的独立连接可以。
    """
    from datetime import datetime, timezone

    from sqlalchemy import text

    _ssh_creds(monkeypatch)
    sample_host.retired_at = datetime.now(timezone.utc)
    db_session.commit()

    resp = client.post(
        "/api/v1/agent/logs",
        json={
            "host_id": sample_host.id,
            "log_path": "/opt/stability-test-agent/logs/agent.log",
            "lines": 10,
        },
    )

    assert resp.status_code == 200, resp.text  # 允许，不是 403/409
    rows = _audits(db_session, sample_host.id)
    assert len(rows) == 1, "退役机尾读必须落审计"
    details = rows[0].details if isinstance(rows[0].details, dict) else {}
    assert details.get("log_path") == "/opt/stability-test-agent/logs/agent.log"

    with engine.connect() as conn:  # 另一个连接 ⇒ 只能看到已提交的数据
        committed = conn.execute(
            text(
                "SELECT count(*) FROM audit_logs"
                " WHERE action = :action AND resource_id = :rid"
            ),
            {"action": "host_retired_log_tail", "rid": str(sample_host.id)},
        ).scalar()
    assert committed == 1, (
        "退役机尾读审计必须已提交：请求结束 get_db() 只 close()，"
        "仅 flush 的行会被回滚（#2047）"
    )


def test_active_host_log_tail_is_not_audited(client, sample_host, db_session, monkeypatch):
    """活跃主机不落该审计——避免把「正常取证」也计入退役审计面。"""
    _ssh_creds(monkeypatch)
    assert sample_host.retired_at is None

    resp = client.post(
        "/api/v1/agent/logs",
        json={
            "host_id": sample_host.id,
            "log_path": "/opt/stability-test-agent/logs/agent.log",
            "lines": 10,
        },
    )

    assert resp.status_code == 200, resp.text
    assert _audits(db_session, sample_host.id) == []
