"""Tests for tools API routes (v2 tool catalog)"""

import os
import pytest


pytestmark = pytest.mark.skipif(
    os.name == "nt",
    reason="Windows + asyncpg + TestClient 写路径不稳定，tools 写接口在 Linux CI 验证",
)


class TestListTools:
    def test_list_tools_response_shape(self, client):
        response = client.get("/api/v1/tools")
        assert response.status_code == 200
        data = response.json()
        assert "data" in data
        assert "error" in data
        assert isinstance(data["data"], list)


class TestCreateAndGetTool:
    def test_create_tool_success(self, client):
        resp = client.post(
            "/api/v1/tools",
            json={
                "name": "MyTool",
                "version": "1.0.0",
                "script_path": "agent/actions/my_tool.py",
                "script_class": "MyToolAction",
                "param_schema": {"type": "object", "properties": {}},
                "description": "tool for test",
                "is_active": True,
            },
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["error"] is None
        assert body["data"]["name"] == "MyTool"
        assert body["data"]["version"] == "1.0.0"

        tool_id = body["data"]["id"]
        get_resp = client.get(f"/api/v1/tools/{tool_id}")
        assert get_resp.status_code == 200
        get_body = get_resp.json()
        assert get_body["error"] is None
        assert get_body["data"]["id"] == tool_id

    def test_get_tool_not_found(self, client):
        resp = client.get("/api/v1/tools/999999")
        assert resp.status_code == 404


class TestUpdateAndDeactivateTool:
    def test_update_and_deactivate_tool(self, client):
        create_resp = client.post(
            "/api/v1/tools",
            json={
                "name": "TempTool",
                "version": "0.1.0",
                "script_path": "agent/actions/temp_tool.py",
                "script_class": "TempToolAction",
                "param_schema": {},
                "is_active": True,
            },
        )
        assert create_resp.status_code == 201
        tool_id = create_resp.json()["data"]["id"]

        update_resp = client.put(
            f"/api/v1/tools/{tool_id}",
            json={"description": "updated", "is_active": True},
        )
        assert update_resp.status_code == 200
        assert update_resp.json()["data"]["description"] == "updated"

        deactivate_resp = client.delete(f"/api/v1/tools/{tool_id}")
        assert deactivate_resp.status_code == 200
        assert deactivate_resp.json()["data"]["deactivated"] == tool_id

    def test_list_tools_filter_active(self, client):
        resp = client.get("/api/v1/tools", params={"is_active": True})
        assert resp.status_code == 200
        body = resp.json()
        assert body["error"] is None
        assert isinstance(body["data"], list)
