"""Tests for notification API routes"""
import pytest


class TestListChannels:
    def test_list_channels_empty(self, client, admin_headers):
        response = client.get("/api/v1/notifications/channels", headers=admin_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0


class TestCreateChannel:
    def test_create_webhook_channel(self, client, admin_headers):
        response = client.post(
            "/api/v1/notifications/channels",
            json={
                "name": "Test Webhook",
                "type": "WEBHOOK",
                "config": {"url": "https://hooks.example.com/test"},
                "enabled": True,
            },
            headers=admin_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Test Webhook"
        assert data["type"] == "WEBHOOK"

    def test_create_channel_missing_name(self, client, admin_headers):
        response = client.post(
            "/api/v1/notifications/channels",
            json={"type": "WEBHOOK", "config": {}},
            headers=admin_headers,
        )
        assert response.status_code == 422


class TestDeleteChannel:
    def test_delete_channel(self, client, admin_headers):
        r = client.post(
            "/api/v1/notifications/channels",
            json={"name": "Del", "type": "WEBHOOK", "config": {"url": "https://x.com"}, "enabled": True},
            headers=admin_headers,
        )
        ch_id = r.json()["id"]
        resp = client.delete(f"/api/v1/notifications/channels/{ch_id}", headers=admin_headers)
        assert resp.status_code == 200


class TestListRules:
    def test_list_rules_empty(self, client, admin_headers):
        response = client.get("/api/v1/notifications/rules", headers=admin_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0


class TestCreateRule:
    def test_create_rule(self, client, admin_headers):
        # Create channel first
        ch = client.post(
            "/api/v1/notifications/channels",
            json={"name": "RuleCh", "type": "WEBHOOK", "config": {"url": "https://x.com"}, "enabled": True},
            headers=admin_headers,
        ).json()

        response = client.post(
            "/api/v1/notifications/rules",
            json={
                "name": "Test Rule",
                "event_type": "RUN_FAILED",
                "channel_id": ch["id"],
                "enabled": True,
            },
            headers=admin_headers,
        )
        assert response.status_code == 200
        assert response.json()["name"] == "Test Rule"


def test_notifications_require_admin(client, auth_headers):
    response = client.get("/api/v1/notifications/channels", headers=auth_headers)
    assert response.status_code == 403


# ── 鉴权闭环 ──────────────────────────────────────────────────────────────
#
# Why: channels / rules 一直挂着 require_admin,但同文件的 4 个 /logs* 端点
#      和 /webhook 曾完全裸奔 —— GET /logs 可匿名读到含主机名/设备序列号的
#      context。下面把"必须鉴权"钉死,避免再次漏挂。

_LOG_ENDPOINTS = [
    ("get", "/api/v1/notifications/logs"),
    ("get", "/api/v1/notifications/logs/unread-count"),
    ("patch", "/api/v1/notifications/logs/1/read"),
    ("post", "/api/v1/notifications/logs/read-all"),
]


@pytest.mark.parametrize("method,path", _LOG_ENDPOINTS)
def test_notification_logs_reject_anonymous(client, method, path):
    resp = getattr(client, method)(path)
    assert resp.status_code == 401, f"{method.upper()} {path} 不应允许匿名访问"


@pytest.mark.parametrize("method,path", _LOG_ENDPOINTS)
def test_notification_logs_allow_regular_user(client, auth_headers, method, path):
    """普通登录用户可用 —— 通知铃铛不该要 admin。"""
    resp = getattr(client, method)(path, headers=auth_headers)
    # 404 = 记录不存在(mark_read 用了不存在的 id),仍说明鉴权已通过
    assert resp.status_code in (200, 404), resp.text


class TestAlertmanagerWebhook:
    def test_webhook_rejects_missing_secret(self, client, monkeypatch):
        monkeypatch.setenv("AGENT_SECRET", "wh-test-secret")
        resp = client.post("/api/v1/notifications/webhook", json={"alerts": []})
        assert resp.status_code == 401

    def test_webhook_rejects_wrong_secret(self, client, monkeypatch):
        monkeypatch.setenv("AGENT_SECRET", "wh-test-secret")
        resp = client.post(
            "/api/v1/notifications/webhook",
            json={"alerts": []},
            headers={"X-Agent-Secret": "nope"},
        )
        assert resp.status_code == 401

    def test_webhook_accepts_agent_secret(self, client, monkeypatch):
        monkeypatch.setenv("AGENT_SECRET", "wh-test-secret")
        resp = client.post(
            "/api/v1/notifications/webhook",
            json={"alerts": []},
            headers={"X-Agent-Secret": "wh-test-secret"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
