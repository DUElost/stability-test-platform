"""Builtin actions catalog API tests."""


def test_list_builtin_actions(client):
    resp = client.get("/api/v1/builtin-actions")
    assert resp.status_code == 200
    body = resp.json()
    assert body["error"] is None
    assert isinstance(body["data"], list)
    names = {item["name"] for item in body["data"]}
    assert "check_device" in names
    assert "ensure_root" in names


def test_update_builtin_action(client):
    update_resp = client.put(
        "/api/v1/builtin-actions/check_device",
        json={
            "label": "Check Device Updated",
            "description": "Updated by test",
            "category": "device",
            "param_schema": {},
            "is_active": True,
        },
    )
    assert update_resp.status_code == 200
    updated = update_resp.json()["data"]
    assert updated["name"] == "check_device"
    assert updated["label"] == "Check Device Updated"
    assert updated["description"] == "Updated by test"
