"""Tests for the system settings overview endpoint (GET /api/v1/settings)."""

from backend.models.notification import AlertRule, ChannelType, EventType, NotificationChannel


class TestSettingsEndpoint:
    def test_requires_auth(self, client):
        response = client.get("/api/v1/settings")
        assert response.status_code == 401

    def test_requires_admin(self, client, auth_headers):
        response = client.get("/api/v1/settings", headers=auth_headers)
        assert response.status_code == 403

    def test_admin_gets_settings(self, client, admin_headers):
        response = client.get("/api/v1/settings", headers=admin_headers)
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["platform_name"]
        assert data["timezone"]
        assert data["database_type"] in ("postgresql", "sqlite")
        assert isinstance(data["database_connected"], bool)
        assert data["agent_heartbeat_interval_seconds"] > 0
        assert data["offline_threshold_seconds"] > 0
        assert isinstance(data["device_offline_notification_enabled"], bool)
        assert isinstance(data["task_failure_notification_enabled"], bool)

    def test_notification_switches_off_when_no_rules(self, client, admin_headers):
        response = client.get("/api/v1/settings", headers=admin_headers)
        assert response.json()["data"]["device_offline_notification_enabled"] is False
        assert response.json()["data"]["task_failure_notification_enabled"] is False

    def test_notification_switch_reflects_enabled_rule(
        self, client, admin_headers, db_session
    ):
        channel = NotificationChannel(
            name="test-channel", type=ChannelType.WEBHOOK, enabled=True
        )
        db_session.add(channel)
        db_session.flush()
        db_session.add(
            AlertRule(
                name="offline-rule",
                event_type=EventType.DEVICE_OFFLINE,
                channel_id=channel.id,
                enabled=True,
            )
        )
        db_session.commit()

        response = client.get("/api/v1/settings", headers=admin_headers)
        data = response.json()["data"]
        assert data["device_offline_notification_enabled"] is True
        # 未建 RUN_FAILED 规则 → 仍为 False
        assert data["task_failure_notification_enabled"] is False

    def test_disabled_rule_does_not_enable_switch(
        self, client, admin_headers, db_session
    ):
        channel = NotificationChannel(
            name="test-channel-2", type=ChannelType.EMAIL, enabled=True
        )
        db_session.add(channel)
        db_session.flush()
        db_session.add(
            AlertRule(
                name="disabled-rule",
                event_type=EventType.RUN_FAILED,
                channel_id=channel.id,
                enabled=False,
            )
        )
        db_session.commit()

        response = client.get("/api/v1/settings", headers=admin_headers)
        assert response.json()["data"]["task_failure_notification_enabled"] is False
