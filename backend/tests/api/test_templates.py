"""Tests for templates API routes (new orchestration migration semantics)"""


class TestListTemplates:
    def test_list_templates(self, client, auth_headers):
        response = client.get("/api/v1/templates", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert "total" in data
        assert "items" in data
        assert data["total"] >= 0


class TestCreateTemplate:
    def test_create_template_returns_503(self, client, auth_headers):
        response = client.post(
            "/api/v1/templates",
            json={
                "name": "Quick Monkey",
                "task_type": "MONKEY",
                "description": "A quick monkey test",
                "params": {"count": 1000, "throttle": 100},
            },
            headers=auth_headers,
        )
        assert response.status_code == 503
        assert "迁移" in response.json()["detail"]

    def test_create_template_missing_name(self, client, auth_headers):
        response = client.post(
            "/api/v1/templates",
            json={"task_type": "MONKEY", "params": {}},
            headers=auth_headers,
        )
        assert response.status_code == 422


class TestUpdateTemplate:
    def test_update_template_returns_503(self, client, auth_headers):
        resp = client.put(
            "/api/v1/templates/1",
            json={"name": "New", "task_type": "MONKEY", "params": {"count": 2000}},
            headers=auth_headers,
        )
        assert resp.status_code == 503
        assert "迁移" in resp.json()["detail"]


class TestDeleteTemplate:
    def test_delete_template_returns_503(self, client, auth_headers):
        resp = client.delete("/api/v1/templates/1", headers=auth_headers)
        assert resp.status_code == 503
        assert "迁移" in resp.json()["detail"]
