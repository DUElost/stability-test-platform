"""Tests for stats API routes"""

from datetime import datetime, timezone

from backend.models.enums import HostStatus
from backend.models.host import Host, Device


class TestDashboardSummary:
    def test_dashboard_summary_empty(self, client):
        response = client.get("/api/v1/stats/dashboard-summary")
        assert response.status_code == 200
        data = response.json()

        assert data["hosts"] == {
            "total": 0,
            "online": 0,
            "offline": 0,
            "degraded": 0,
            "avg_cpu_load": 0.0,
            "avg_ram_usage": 0.0,
            "avg_disk_usage": 0.0,
            "online_rate": 0.0,
        }
        assert data["devices"] == {
            "total": 0,
            "idle": 0,
            "testing": 0,
            "offline": 0,
            "error": 0,
            "low_battery": 0,
            "high_temp": 0,
        }
        assert data["alerts"] == {
            "total": 0,
            "low_battery": 0,
            "high_temp": 0,
            "error": 0,
        }
        assert data["host_resources"] == []

    def test_dashboard_summary_aggregates_hosts_devices_and_alerts(
        self, client, db_session, sample_host,
    ):
        sample_host.status = HostStatus.ONLINE.value
        sample_host.last_heartbeat = datetime.now(timezone.utc)
        sample_host.extra = {
            "cpu_load": 12.5,
            "ram_usage": 48.0,
            "disk_usage": {"usage_percent": 77.7},
        }

        device_idle = Device(
            serial="DEV-IDLE-1",
            status="ONLINE",
            host_id=sample_host.id,
            battery_level=10,
            temperature=46,
        )
        device_busy = Device(
            serial="DEV-BUSY-1",
            status="BUSY",
            host_id=sample_host.id,
            battery_level=88,
            temperature=32,
        )
        device_offline = Device(
            serial="DEV-OFF-1",
            status="OFFLINE",
            host_id=sample_host.id,
            battery_level=15,
            temperature=50,
        )
        db_session.add_all([device_idle, device_busy, device_offline])
        db_session.commit()

        response = client.get("/api/v1/stats/dashboard-summary")
        assert response.status_code == 200
        data = response.json()

        assert data["hosts"]["total"] == 1
        assert data["hosts"]["online"] == 1
        assert data["hosts"]["avg_cpu_load"] == 12.5
        assert data["devices"]["total"] == 3
        assert data["devices"]["idle"] == 1
        assert data["devices"]["testing"] == 1
        assert data["devices"]["offline"] == 1
        assert data["devices"]["low_battery"] == 2
        assert data["devices"]["high_temp"] == 2
        assert data["alerts"]["total"] == 4
        assert data["host_resources"][0]["ip"] == "172.21.15.100"

    def test_null_battery_not_counted_as_low(self, client, db_session, sample_host):
        """回归: NULL 电量不应被误算为低电量告警"""
        device_no_battery = Device(
            serial="DEV-NULL-BATT",
            status="ONLINE",
            host_id=sample_host.id,
            battery_level=None,
            temperature=None,
        )
        db_session.add(device_no_battery)
        db_session.commit()

        response = client.get("/api/v1/stats/dashboard-summary")
        assert response.status_code == 200
        data = response.json()
        assert data["devices"]["total"] == 1
        assert data["devices"]["low_battery"] == 0
        assert data["devices"]["high_temp"] == 0
        assert data["alerts"]["total"] == 0


class TestActivityStats:
    def test_activity_default(self, client):
        response = client.get("/api/v1/stats/activity")
        assert response.status_code == 200
        data = response.json()
        assert "points" in data
        assert "hours" in data

    def test_activity_custom_hours(self, client):
        response = client.get("/api/v1/stats/activity", params={"hours": 48})
        assert response.status_code == 200
        assert response.json()["hours"] == 48


class TestCompletionTrend:
    def test_completion_trend_default(self, client):
        response = client.get("/api/v1/stats/completion-trend")
        assert response.status_code == 200
        data = response.json()
        assert "points" in data
        assert "days" in data

    def test_completion_trend_custom_days(self, client):
        response = client.get("/api/v1/stats/completion-trend", params={"days": 14})
        assert response.status_code == 200
        assert response.json()["days"] == 14
