"""Tests for tools API routes"""
import pytest


class TestListCategories:
    def test_list_categories_empty(self, client):
        response = client.get("/api/v1/tools/categories")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert data["items"] == []


class TestCreateCategory:
    def test_create_category(self, client, auth_headers):
        response = client.post(
            "/api/v1/tools/categories",
            json={"name": "Stress", "description": "Stress tests", "icon": "zap", "order": 1, "enabled": True},
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Stress"
        assert data["tools_count"] == 0

    def test_create_duplicate_category(self, client, auth_headers):
        client.post("/api/v1/tools/categories", json={"name": "Dup", "enabled": True}, headers=auth_headers)
        resp2 = client.post("/api/v1/tools/categories", json={"name": "Dup", "enabled": True}, headers=auth_headers)
        assert resp2.status_code == 400


class TestUpdateCategory:
    def test_update_category(self, client, auth_headers):
        r = client.post("/api/v1/tools/categories", json={"name": "Old", "enabled": True}, headers=auth_headers)
        cat_id = r.json()["id"]
        resp = client.put(f"/api/v1/tools/categories/{cat_id}", json={"name": "New", "enabled": True}, headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["name"] == "New"

    def test_update_category_not_found(self, client, auth_headers):
        resp = client.put("/api/v1/tools/categories/99999", json={"name": "X", "enabled": True}, headers=auth_headers)
        assert resp.status_code == 404


class TestDeleteCategory:
    def test_delete_category(self, client, auth_headers):
        r = client.post("/api/v1/tools/categories", json={"name": "Del", "enabled": True}, headers=auth_headers)
        cat_id = r.json()["id"]
        resp = client.delete(f"/api/v1/tools/categories/{cat_id}", headers=auth_headers)
        assert resp.status_code == 200

    def test_delete_category_not_found(self, client, auth_headers):
        resp = client.delete("/api/v1/tools/categories/99999", headers=auth_headers)
        assert resp.status_code == 404


class TestListTools:
    def test_list_tools_empty(self, client):
        response = client.get("/api/v1/tools")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0

    def test_list_tools_by_category(self, client, auth_headers):
        cat = client.post("/api/v1/tools/categories", json={"name": "Cat1", "enabled": True}, headers=auth_headers).json()
        client.post("/api/v1/tools", json={
            "category_id": cat["id"], "name": "Tool1", "script_path": "/t.py",
            "script_type": "python", "default_params": {}, "param_schema": {},
            "timeout": 600, "need_device": True, "enabled": True,
        }, headers=auth_headers)
        resp = client.get("/api/v1/tools", params={"category_id": cat["id"]})
        assert resp.status_code == 200
        assert resp.json()["total"] == 1


class TestCreateTool:
    def test_create_tool(self, client, auth_headers):
        cat = client.post("/api/v1/tools/categories", json={"name": "CatT", "enabled": True}, headers=auth_headers).json()
        resp = client.post("/api/v1/tools", json={
            "category_id": cat["id"], "name": "MyTool", "script_path": "/x.py",
            "script_type": "python", "default_params": {}, "param_schema": {},
            "timeout": 300, "need_device": False, "enabled": True,
        }, headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["name"] == "MyTool"

    def test_create_tool_bad_category(self, client, auth_headers):
        resp = client.post("/api/v1/tools", json={
            "category_id": 99999, "name": "Bad", "script_path": "/x.py",
            "script_type": "python", "default_params": {}, "param_schema": {},
            "timeout": 300, "need_device": False, "enabled": True,
        }, headers=auth_headers)
        assert resp.status_code == 400
