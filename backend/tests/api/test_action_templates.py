"""可复用 Action 模板 API 测试。"""

import pytest
from uuid import uuid4


def _uniq(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:8]}"


def _create_tool(client, name: str = "TemplateTool", version: str = "1.0.0") -> int:
    resp = client.post(
        "/api/v1/tools",
        json={
            "name": name,
            "version": version,
            "script_path": "agent/actions/template_tool.py",
            "script_class": "TemplateToolAction",
            "param_schema": {"type": "object", "properties": {}},
            "is_active": True,
        },
    )
    assert resp.status_code == 201
    return resp.json()["data"]["id"]


def test_action_template_crud(client):
    tool_id = _create_tool(client, name=_uniq("TemplateToolA"), version="2.0.0")
    template_name = _uniq("root_check_template")

    create_resp = client.post(
        "/api/v1/action-templates",
        json={
            "name": template_name,
            "description": "检查设备并确保 root",
            "action": f"tool:{tool_id}",
            "version": "2.0.0",
            "params": {"ensure_root": True},
            "timeout_seconds": 180,
            "retry": 1,
            "is_active": True,
        },
    )
    assert create_resp.status_code == 201
    created = create_resp.json()["data"]
    assert created["name"] == template_name
    assert created["action"] == f"tool:{tool_id}"
    assert created["version"] == "2.0.0"

    template_id = created["id"]
    get_resp = client.get(f"/api/v1/action-templates/{template_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["data"]["id"] == template_id

    update_resp = client.put(
        f"/api/v1/action-templates/{template_id}",
        json={"retry": 2, "description": "updated"},
    )
    assert update_resp.status_code == 200
    updated = update_resp.json()["data"]
    assert updated["retry"] == 2
    assert updated["description"] == "updated"

    deactivate_resp = client.delete(f"/api/v1/action-templates/{template_id}")
    assert deactivate_resp.status_code == 200
    assert deactivate_resp.json()["data"]["deactivated"] == template_id


def test_action_template_validation(client):
    # builtin 不允许 version
    invalid_builtin = client.post(
        "/api/v1/action-templates",
        json={
            "name": _uniq("invalid_builtin_with_version"),
            "action": "builtin:check_device",
            "version": "1.0.0",
            "params": {},
            "timeout_seconds": 60,
            "retry": 0,
        },
    )
    assert invalid_builtin.status_code == 422

    # tool action 必须有 version
    tool_id = _create_tool(client, name=_uniq("TemplateToolB"), version="1.1.0")
    missing_version = client.post(
        "/api/v1/action-templates",
        json={
            "name": _uniq("invalid_tool_without_version"),
            "action": f"tool:{tool_id}",
            "params": {},
            "timeout_seconds": 60,
            "retry": 0,
        },
    )
    assert missing_version.status_code == 422
