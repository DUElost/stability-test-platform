"""Tests for templates API routes"""
import pytest


class TestListTemplates:
    def test_list_templates_empty(self, client, auth_headers):
        response = client.get("/api/v1/templates", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0


class TestCreateTemplate:
    def test_create_template(self, client, auth_headers):
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
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Quick Monkey"

    def test_create_template_missing_name(self, client, auth_headers):
        response = client.post(
            "/api/v1/templates",
            json={"task_type": "MONKEY", "params": {}},
            headers=auth_headers,
        )
        assert response.status_code == 422


class TestUpdateTemplate:
    def test_update_template(self, client, auth_headers):
        r = client.post(
            "/api/v1/templates",
            json={"name": "Old", "task_type": "MONKEY", "params": {}},
            headers=auth_headers,
        )
        tmpl_id = r.json()["id"]
        resp = client.put(
            f"/api/v1/templates/{tmpl_id}",
            json={"name": "New", "task_type": "MONKEY", "params": {"count": 2000}},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "New"


class TestDeleteTemplate:
    def test_delete_template(self, client, auth_headers):
        r = client.post(
            "/api/v1/templates",
            json={"name": "Del", "task_type": "MONKEY", "params": {}},
            headers=auth_headers,
        )
        tmpl_id = r.json()["id"]
        resp = client.delete(f"/api/v1/templates/{tmpl_id}", headers=auth_headers)
        assert resp.status_code == 200

    def test_delete_template_not_found(self, client, auth_headers):
        resp = client.delete("/api/v1/templates/99999", headers=auth_headers)
        assert resp.status_code == 404
