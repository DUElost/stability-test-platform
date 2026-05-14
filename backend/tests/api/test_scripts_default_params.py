"""Script default_params versioning constraint tests — ADR-0020."""

from uuid import uuid4


def _uniq(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:8]}"


class TestScriptDefaultParamsAPI:
    def test_create_with_default_params(self, client, admin_headers):
        name = _uniq("dp_test")
        payload = {
            "name": name, "display_name": "DP Test",
            "category": "device", "script_type": "python",
            "version": "1.0.0",
            "nfs_path": f"/scripts/{name}/main.py",
            "content_sha256": "a" * 64,
            "param_schema": {},
            "default_params": {"timeout": 30, "retry": 2},
        }
        resp = client.post("/api/v1/scripts", json=payload, headers=admin_headers)
        assert resp.status_code == 201, resp.text
        data = resp.json()["data"]
        assert data["default_params"] == {"timeout": 30, "retry": 2}

    def test_update_rejects_default_params_change(self, client, admin_headers):
        name = _uniq("dp_lock")
        payload = {
            "name": name, "display_name": "DP Lock",
            "category": "device", "script_type": "python",
            "version": "1.0.0",
            "nfs_path": f"/scripts/{name}/main.py",
            "content_sha256": "b" * 64,
            "param_schema": {},
            "default_params": {"timeout": 30},
        }
        create = client.post("/api/v1/scripts", json=payload, headers=admin_headers)
        assert create.status_code == 201
        script_id = create.json()["data"]["id"]

        # Try to change default_params — must fail
        update_resp = client.put(f"/api/v1/scripts/{script_id}", json={
            "default_params": {"timeout": 60},
        }, headers=admin_headers)
        assert update_resp.status_code == 422, update_resp.text

    def test_update_allows_same_default_params(self, client, admin_headers):
        name = _uniq("dp_same")
        payload = {
            "name": name, "display_name": "DP Same",
            "category": "device", "script_type": "python",
            "version": "1.0.0",
            "nfs_path": f"/scripts/{name}/main.py",
            "content_sha256": "c" * 64,
            "param_schema": {},
            "default_params": {"timeout": 30},
        }
        create = client.post("/api/v1/scripts", json=payload, headers=admin_headers)
        assert create.status_code == 201
        script_id = create.json()["data"]["id"]

        # Same value — should succeed
        update_resp = client.put(f"/api/v1/scripts/{script_id}", json={
            "default_params": {"timeout": 30},
        }, headers=admin_headers)
        assert update_resp.status_code == 200, update_resp.text

    def test_param_schema_is_still_mutable(self, client, admin_headers):
        name = _uniq("dp_schema")
        payload = {
            "name": name, "display_name": "DP Schema",
            "category": "device", "script_type": "python",
            "version": "1.0.0",
            "nfs_path": f"/scripts/{name}/main.py",
            "content_sha256": "d" * 64,
            "param_schema": {"timeout": {"type": "int"}},
            "default_params": {"timeout": 30},
        }
        create = client.post("/api/v1/scripts", json=payload, headers=admin_headers)
        assert create.status_code == 201
        script_id = create.json()["data"]["id"]

        # param_schema should be freely modifiable
        update_resp = client.put(f"/api/v1/scripts/{script_id}", json={
            "param_schema": {"timeout": {"type": "int"}, "retry": {"type": "int"}},
        }, headers=admin_headers)
        assert update_resp.status_code == 200
        assert "retry" in update_resp.json()["data"]["param_schema"]

    def test_version_create_requires_default_params(self, client, admin_headers):
        name = _uniq("dp_ver")
        payload = {
            "name": name, "display_name": "DP Ver",
            "category": "device", "script_type": "python",
            "version": "1.0.0",
            "nfs_path": f"/scripts/{name}/v1.0.0/main.py",
            "content_sha256": "e" * 64,
            "param_schema": {},
            "default_params": {"timeout": 10},
        }
        create = client.post("/api/v1/scripts", json=payload, headers=admin_headers)
        assert create.status_code == 201

        # Create new version with different default_params
        ver_resp = client.post(f"/api/v1/scripts/{name}/versions", json={
            "version": "2.0.0",
            "nfs_path": f"/scripts/{name}/v2.0.0/main.py",
            "content_sha256": "f" * 64,
            "param_schema": {},
            "default_params": {"timeout": 60},
        }, headers=admin_headers)
        assert ver_resp.status_code == 201, ver_resp.text
        assert ver_resp.json()["data"]["version"] == "2.0.0"
        assert ver_resp.json()["data"]["default_params"] == {"timeout": 60}

    def test_list_scripts_includes_default_params(self, client, admin_headers, auth_headers):
        name = _uniq("dp_list")
        payload = {
            "name": name, "display_name": "DP List",
            "category": "device", "script_type": "python",
            "version": "1.0.0",
            "nfs_path": f"/scripts/{name}/main.py",
            "content_sha256": "g" * 64,
            "param_schema": {},
            "default_params": {"timeout": 30},
        }
        client.post("/api/v1/scripts", json=payload, headers=admin_headers)
        resp = client.get("/api/v1/scripts", params={"is_active": True}, headers=auth_headers)
        assert resp.status_code == 200
        items = resp.json()["data"]
        match = [s for s in items if s["name"] == name]
        assert len(match) == 1
        assert match[0]["default_params"] == {"timeout": 30}
