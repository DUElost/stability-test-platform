"""Tests for results API routes"""


class TestResultsSummary:
    def test_summary_empty(self, client):
        response = client.get("/api/v1/results/summary")
        assert response.status_code == 200
        data = response.json()
        assert data["runs_by_status"]["total"] == 0
        assert data["test_type_stats"] == []
        assert data["risk_distribution"]["high"] == 0
        assert data["recent_runs"] == []

    def test_summary_with_limit(self, client):
        response = client.get("/api/v1/results/summary", params={"limit": 5})
        assert response.status_code == 200
