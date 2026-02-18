"""Tests for stats API routes"""


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
