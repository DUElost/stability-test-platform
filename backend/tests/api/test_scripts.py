"""Script catalog API tests."""

from uuid import uuid4


def _uniq(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:8]}"


def test_script_crud_and_soft_delete(client, admin_headers, auth_headers):
    name = _uniq("push_bundle")
    payload = {
        "name": name,
        "display_name": "Push Bundle",
        "category": "resource",
        "script_type": "python",
        "version": "1.0.0",
        "nfs_path": "/mnt/storage/test-platform/scripts/resource/push_bundle/v1.0.0/push_bundle.py",
        "content_sha256": "a" * 64,
        "param_schema": {"bundle_name": {"type": "string", "required": True}},
        "description": "Push resource bundle",
        "is_active": True,
    }

    create_resp = client.post("/api/v1/scripts", json=payload, headers=admin_headers)
    assert create_resp.status_code == 201
    created = create_resp.json()["data"]
    assert created["name"] == name
    assert created["version"] == "1.0.0"
    assert created["is_active"] is True

    duplicate_resp = client.post("/api/v1/scripts", json=payload, headers=admin_headers)
    assert duplicate_resp.status_code == 409

    script_id = created["id"]
    update_resp = client.put(
        f"/api/v1/scripts/{script_id}",
        json={"display_name": "Push Bundle Updated", "is_active": False},
        headers=admin_headers,
    )
    assert update_resp.status_code == 200
    assert update_resp.json()["data"]["display_name"] == "Push Bundle Updated"
    assert update_resp.json()["data"]["is_active"] is False

    get_resp = client.get(f"/api/v1/scripts/{script_id}", headers=auth_headers)
    assert get_resp.status_code == 200

    delete_resp = client.delete(f"/api/v1/scripts/{script_id}", headers=admin_headers)
    assert delete_resp.status_code == 200
    assert delete_resp.json()["data"]["deactivated"] == script_id


def test_script_scan_registers_conflicts_and_deactivates_missing(
    client, tmp_path, monkeypatch, admin_headers, auth_headers
):
    root = tmp_path / "scripts"
    version_dir = root / "connect_wifi" / "v1.0.0"
    version_dir.mkdir(parents=True)
    entry = version_dir / "connect_wifi.sh"
    entry.write_text("#!/usr/bin/env bash\necho wifi\n", encoding="utf-8")

    monkeypatch.setenv("STP_SCRIPT_ROOT", str(root))

    first_scan = client.post("/api/v1/scripts/scan", headers=admin_headers)
    assert first_scan.status_code == 200
    first_data = first_scan.json()["data"]
    assert first_data["created"] == 1
    assert first_data["skipped"] == 0
    assert first_data["deactivated"] == 0
    assert first_data["conflicts"] == []

    list_resp = client.get("/api/v1/scripts", params={"is_active": True}, headers=auth_headers)
    assert list_resp.status_code == 200
    scripts = list_resp.json()["data"]
    assert len(scripts) == 1
    assert scripts[0]["name"] == "connect_wifi"
    assert scripts[0]["category"] == "device"
    assert scripts[0]["version"] == "1.0.0"
    assert scripts[0]["script_type"] == "shell"

    second_scan = client.post("/api/v1/scripts/scan", headers=admin_headers)
    assert second_scan.status_code == 200
    assert second_scan.json()["data"]["skipped"] == 1

    entry.write_text("#!/usr/bin/env bash\necho changed\n", encoding="utf-8")
    conflict_scan = client.post("/api/v1/scripts/scan", headers=admin_headers)
    assert conflict_scan.status_code == 200
    conflicts = conflict_scan.json()["data"]["conflicts"]
    assert conflicts == [{"name": "connect_wifi", "version": "1.0.0"}]

    entry.unlink()
    inactive_scan = client.post("/api/v1/scripts/scan", headers=admin_headers)
    assert inactive_scan.status_code == 200
    assert inactive_scan.json()["data"]["deactivated"] == 1

    inactive_list = client.get("/api/v1/scripts", params={"is_active": True}, headers=auth_headers)
    assert inactive_list.status_code == 200
    assert inactive_list.json()["data"] == []


def test_script_scan_maps_source_root_to_agent_runtime_root(
    client, tmp_path, monkeypatch, admin_headers, auth_headers
):
    root = tmp_path / "agent" / "scripts"
    version_dir = root / "connect_wifi" / "v1.0.0"
    version_dir.mkdir(parents=True)
    entry = version_dir / "connect_wifi.sh"
    entry.write_text("#!/usr/bin/env bash\necho wifi\n", encoding="utf-8")

    monkeypatch.setenv("STP_SCRIPT_ROOT", str(root))
    monkeypatch.setenv("STP_SCRIPT_RUNTIME_ROOT", "/opt/stability-test-agent/agent/scripts")

    scan_resp = client.post("/api/v1/scripts/scan", headers=admin_headers)
    assert scan_resp.status_code == 200

    list_resp = client.get("/api/v1/scripts", params={"is_active": True}, headers=auth_headers)
    assert list_resp.status_code == 200
    scripts = list_resp.json()["data"]
    assert len(scripts) == 1
    assert (
        scripts[0]["nfs_path"]
        == "/opt/stability-test-agent/agent/scripts/connect_wifi/v1.0.0/connect_wifi.sh"
    )


def test_script_endpoints_require_auth_and_admin_for_writes(client, admin_headers, auth_headers):
    list_resp = client.get("/api/v1/scripts")
    assert list_resp.status_code == 401

    list_authed = client.get("/api/v1/scripts", headers=auth_headers)
    assert list_authed.status_code == 200

    create_resp = client.post(
        "/api/v1/scripts",
        json={
            "name": _uniq("forbidden_script"),
            "script_type": "python",
            "version": "1.0.0",
            "nfs_path": "/tmp/forbidden.py",
            "content_sha256": "f" * 64,
        },
        headers=auth_headers,
    )
    assert create_resp.status_code == 403
