"""Script sequence facade API tests."""

from uuid import uuid4


def _uniq(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:8]}"


def _script_payload(name: str) -> dict:
    return {
        "name": name,
        "display_name": name,
        "category": "device",
        "script_type": "python",
        "version": "1.0.0",
        "nfs_path": f"/mnt/storage/test-platform/scripts/device/{name}/v1.0.0/{name}.py",
        "entry_point": "",
        "content_sha256": "b" * 64,
        "param_schema": {"ssid": {"type": "string", "required": True}},
        "description": "Device setup script",
        "is_active": True,
    }


def test_script_sequence_crud_validates_active_scripts(client):
    script_name = _uniq("connect_wifi")
    script_resp = client.post("/api/v1/scripts", json=_script_payload(script_name))
    assert script_resp.status_code == 201

    create_resp = client.post(
        "/api/v1/script-sequences",
        json={
            "name": "Smoke sequence",
            "description": "Connect WiFi before test",
            "on_failure": "stop",
            "items": [
                {
                    "script_name": script_name,
                    "version": "1.0.0",
                    "params": {"ssid": "TestNet"},
                    "timeout_seconds": 30,
                    "retry": 1,
                }
            ],
        },
    )

    assert create_resp.status_code == 201
    created = create_resp.json()["data"]
    assert created["id"] > 0
    assert created["items"][0]["script_name"] == script_name
    assert created["items"][0]["timeout_seconds"] == 30
    assert created["on_failure"] == "stop"

    list_resp = client.get("/api/v1/script-sequences")
    assert list_resp.status_code == 200
    assert any(item["id"] == created["id"] for item in list_resp.json()["data"]["items"])

    search_resp = client.get("/api/v1/script-sequences", params={"q": "Smoke"})
    assert search_resp.status_code == 200
    assert any(item["id"] == created["id"] for item in search_resp.json()["data"]["items"])

    update_resp = client.put(
        f"/api/v1/script-sequences/{created['id']}",
        json={"description": "Updated", "items": created["items"], "on_failure": "stop"},
    )
    assert update_resp.status_code == 200
    assert update_resp.json()["data"]["description"] == "Updated"


def test_script_sequence_rejects_missing_active_script(client):
    resp = client.post(
        "/api/v1/script-sequences",
        json={
            "name": "Broken sequence",
            "items": [
                {
                    "script_name": "missing_script",
                    "version": "1.0.0",
                    "params": {},
                    "timeout_seconds": 30,
                }
            ],
        },
    )

    assert resp.status_code == 400
    assert "missing_script:1.0.0" in resp.json()["detail"]
