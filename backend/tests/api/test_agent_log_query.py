"""#940 — POST /api/v1/agent/logs host_id 字符串契约。

Host.id 是 String(64) PK；AgentLogQuery/AgentLogOut 曾用 int——非数字主机 ID
在请求校验阶段就被 422 拒绝，无法进入查询。契约：字符串 host_id 可查询，
输出原样回显。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest


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
