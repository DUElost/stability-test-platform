"""Tests for notification API routes"""
import pytest


class TestListChannels:
    def test_list_channels_empty(self, client, auth_headers):
        response = client.get("/api/v1/notifications/channels", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0


class TestCreateChannel:
    def test_create_webhook_channel(self, client, auth_headers):
        response = client.post(
            "/api/v1/notifications/channels",
            json={
                "name": "Test Webhook",
                "type": "WEBHOOK",
                "config": {"url": "https://hooks.example.com/test"},
                "enabled": True,
            },
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Test Webhook"
        assert data["type"] == "WEBHOOK"

    def test_create_channel_missing_name(self, client, auth_headers):
        response = client.post(
            "/api/v1/notifications/channels",
            json={"type": "WEBHOOK", "config": {}},
            headers=auth_headers,
        )
        assert response.status_code == 422


class TestDeleteChannel:
    def test_delete_channel(self, client, auth_headers):
        r = client.post(
            "/api/v1/notifications/channels",
            json={"name": "Del", "type": "WEBHOOK", "config": {"url": "https://x.com"}, "enabled": True},
            headers=auth_headers,
        )
        ch_id = r.json()["id"]
        resp = client.delete(f"/api/v1/notifications/channels/{ch_id}", headers=auth_headers)
        assert resp.status_code == 200


class TestListRules:
    def test_list_rules_empty(self, client, auth_headers):
        response = client.get("/api/v1/notifications/rules", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0


class TestCreateRule:
    def test_create_rule(self, client, auth_headers):
        # Create channel first
        ch = client.post(
            "/api/v1/notifications/channels",
            json={"name": "RuleCh", "type": "WEBHOOK", "config": {"url": "https://x.com"}, "enabled": True},
            headers=auth_headers,
        ).json()

        response = client.post(
            "/api/v1/notifications/rules",
            json={
                "name": "Test Rule",
                "event_type": "RUN_FAILED",
                "channel_id": ch["id"],
                "enabled": True,
            },
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["name"] == "Test Rule"
