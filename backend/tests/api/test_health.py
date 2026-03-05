"""Tests for health endpoint"""


class TestHealth:
    def test_health_returns_200(self, client):
        response = client.get("/health")
        assert response.status_code in (200, 503)
        data = response.json()
        if response.status_code == 200:
            assert "data" in data
            assert data["data"]["status"] == "healthy"
            assert data["error"] is None
        else:
            assert data["data"] is None
            assert data["error"]["code"] == "DB_UNAVAILABLE"
