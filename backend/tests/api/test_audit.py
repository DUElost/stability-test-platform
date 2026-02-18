"""Tests for audit log API routes"""


class TestAuditLogs:
    def test_list_audit_logs(self, client, admin_headers):
        response = client.get("/api/v1/audit-logs", headers=admin_headers)
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert "total" in data

    def test_list_audit_logs_with_filters(self, client, admin_headers):
        response = client.get(
            "/api/v1/audit-logs",
            params={"resource_type": "task", "action": "create"},
            headers=admin_headers,
        )
        assert response.status_code == 200

    def test_list_audit_logs_requires_auth(self, client):
        response = client.get("/api/v1/audit-logs")
        assert response.status_code in (401, 403)
