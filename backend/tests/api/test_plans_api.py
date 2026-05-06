"""Plan CRUD + dispatch API tests — ADR-0020."""

from uuid import uuid4


def _uniq(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:8]}"


LIFECYCLE_MINIMAL = {
    "init": [{"step_id": "check_device", "action": "script:check_device",
              "version": "1.0.0", "timeout_seconds": 30}],
    "teardown": [],
}


class TestPlanCRUD:
    def test_create_and_get_plan(self, client, auth_headers, sample_script):
        name = _uniq("plan")
        payload = {
            "name": name,
            "lifecycle": LIFECYCLE_MINIMAL,
            "steps": [
                {"step_key": "init_0", "script_name": "check_device",
                 "script_version": "1.0.0", "stage": "init", "sort_order": 0},
            ],
        }
        resp = client.post("/api/v1/plans", json=payload, headers=auth_headers)
        assert resp.status_code == 201, resp.text
        data = resp.json()["data"]
        assert data["name"] == name
        assert len(data["steps"]) == 1
        assert data["steps"][0]["step_key"] == "init_0"

        get_resp = client.get(f"/api/v1/plans/{data['id']}")
        assert get_resp.status_code == 200
        assert get_resp.json()["data"]["name"] == name

    def test_list_plans(self, client, auth_headers):
        name = _uniq("plan")
        client.post("/api/v1/plans", json={
            "name": name, "lifecycle": LIFECYCLE_MINIMAL, "steps": [],
        }, headers=auth_headers)

        resp = client.get("/api/v1/plans")
        assert resp.status_code == 200
        items = resp.json()["data"]
        assert any(p["name"] == name for p in items)

    def test_update_plan(self, client, auth_headers, sample_script):
        name = _uniq("plan")
        create = client.post("/api/v1/plans", json={
            "name": name, "lifecycle": LIFECYCLE_MINIMAL, "steps": [],
        }, headers=auth_headers)
        plan_id = create.json()["data"]["id"]

        update = client.put(f"/api/v1/plans/{plan_id}", json={
            "name": f"{name}_updated",
            "steps": [
                {"step_key": "new_step", "script_name": "check_device",
                 "script_version": "1.0.0", "stage": "init", "sort_order": 0},
            ],
        }, headers=auth_headers)
        assert update.status_code == 200
        updated = update.json()["data"]
        assert updated["name"] == f"{name}_updated"
        assert len(updated["steps"]) == 1
        assert updated["steps"][0]["step_key"] == "new_step"

    def test_delete_plan(self, client, auth_headers):
        name = _uniq("plan")
        create = client.post("/api/v1/plans", json={
            "name": name, "lifecycle": LIFECYCLE_MINIMAL, "steps": [],
        }, headers=auth_headers)
        plan_id = create.json()["data"]["id"]

        delete = client.delete(f"/api/v1/plans/{plan_id}", headers=auth_headers)
        assert delete.status_code == 200
        assert delete.json()["data"]["deleted"] == plan_id

        get_resp = client.get(f"/api/v1/plans/{plan_id}")
        assert get_resp.status_code == 404

    def test_create_invalid_lifecycle_rejected(self, client, auth_headers):
        payload = {
            "name": _uniq("bad"),
            "lifecycle": {"init": [], "teardown": []},  # empty init
            "steps": [],
        }
        resp = client.post("/api/v1/plans", json=payload, headers=auth_headers)
        assert resp.status_code == 422, resp.text

    def test_next_plan_self_reference_rejected(self, client, auth_headers):
        name = _uniq("self")
        create = client.post("/api/v1/plans", json={
            "name": name, "lifecycle": LIFECYCLE_MINIMAL, "steps": [],
        }, headers=auth_headers)
        plan_id = create.json()["data"]["id"]
        resp = client.put(f"/api/v1/plans/{plan_id}", json={
            "next_plan_id": plan_id,
        }, headers=auth_headers)
        assert resp.status_code == 422


class TestPlanDispatch:
    def test_preview_requires_existing_plan(self, client, auth_headers):
        resp = client.post("/api/v1/plans/99999/run/preview", json={
            "device_ids": [1],
        }, headers=auth_headers)
        assert resp.status_code == 400

    def test_run_requires_existing_plan(self, client, auth_headers):
        resp = client.post("/api/v1/plans/99999/run", json={
            "device_ids": [1],
        }, headers=auth_headers)
        assert resp.status_code == 400
