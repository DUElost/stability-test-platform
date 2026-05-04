"""可复用 Action 模板 API 测试。"""

import pytest
from uuid import uuid4


def _uniq(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:8]}"


def test_action_template_crud(client):
    template_name = _uniq("root_check_template")

    create_resp = client.post(
        "/api/v1/action-templates",
        json={
            "name": template_name,
            "description": "检查设备并确保 root",
            "action": "script:check_device",
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
    assert created["action"] == "script:check_device"
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
    # 只允许 script action
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

    invalid_tool = client.post(
        "/api/v1/action-templates",
        json={
            "name": _uniq("invalid_tool_action"),
            "action": "tool:1",
            "version": "1.0.0",
            "params": {},
            "timeout_seconds": 60,
            "retry": 0,
        },
    )
    assert invalid_tool.status_code == 422

    # script action 必须有 version
    missing_script_version = client.post(
        "/api/v1/action-templates",
        json={
            "name": _uniq("invalid_script_without_version"),
            "action": "script:push_bundle",
            "params": {},
            "timeout_seconds": 60,
            "retry": 0,
        },
    )
    assert missing_script_version.status_code == 422

    valid_script = client.post(
        "/api/v1/action-templates",
        json={
            "name": _uniq("valid_script_template"),
            "action": "script:push_bundle",
            "version": "2.0.0",
            "params": {"bundle_name": "audio_stability_v2"},
            "timeout_seconds": 600,
            "retry": 0,
        },
    )
    assert valid_script.status_code == 201
    assert valid_script.json()["data"]["action"] == "script:push_bundle"
    assert valid_script.json()["data"]["version"] == "2.0.0"
