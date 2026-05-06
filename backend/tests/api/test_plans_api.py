"""Plan CRUD + dispatch API tests — ADR-0020."""

from uuid import uuid4


def _uniq(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:8]}"


def _minimal_steps() -> list[dict]:
    return [
        {"step_key": "init_0", "script_name": "check_device",
         "script_version": "1.0.0", "stage": "init", "sort_order": 0,
         "timeout_seconds": 30},
    ]


class TestPlanCRUD:
    def test_create_and_get_plan(self, client, auth_headers, sample_script):
        name = _uniq("plan")
        payload = {"name": name, "steps": _minimal_steps()}
        resp = client.post("/api/v1/plans", json=payload, headers=auth_headers)
        assert resp.status_code == 201, resp.text
        data = resp.json()["data"]
        assert data["name"] == name
        assert "lifecycle" not in data  # ADR-0020 §2 唯一事实源
        assert len(data["steps"]) == 1
        assert data["steps"][0]["step_key"] == "init_0"
        assert data["steps"][0]["enabled"] is True

        get_resp = client.get(f"/api/v1/plans/{data['id']}")
        assert get_resp.status_code == 200
        assert get_resp.json()["data"]["name"] == name

    def test_list_plans(self, client, auth_headers, sample_script):
        name = _uniq("plan")
        client.post("/api/v1/plans", json={
            "name": name, "steps": _minimal_steps(),
        }, headers=auth_headers)

        resp = client.get("/api/v1/plans")
        assert resp.status_code == 200
        items = resp.json()["data"]
        assert any(p["name"] == name for p in items)

    def test_update_plan(self, client, auth_headers, sample_script):
        name = _uniq("plan")
        create = client.post("/api/v1/plans", json={
            "name": name, "steps": _minimal_steps(),
        }, headers=auth_headers)
        plan_id = create.json()["data"]["id"]

        update = client.put(f"/api/v1/plans/{plan_id}", json={
            "name": f"{name}_updated",
            "steps": [
                {"step_key": "new_step", "script_name": "check_device",
                 "script_version": "1.0.0", "stage": "init", "sort_order": 0,
                 "timeout_seconds": 30},
            ],
        }, headers=auth_headers)
        assert update.status_code == 200
        updated = update.json()["data"]
        assert updated["name"] == f"{name}_updated"
        assert len(updated["steps"]) == 1
        assert updated["steps"][0]["step_key"] == "new_step"

    def test_delete_plan(self, client, auth_headers, sample_script):
        name = _uniq("plan")
        create = client.post("/api/v1/plans", json={
            "name": name, "steps": _minimal_steps(),
        }, headers=auth_headers)
        plan_id = create.json()["data"]["id"]

        delete = client.delete(f"/api/v1/plans/{plan_id}", headers=auth_headers)
        assert delete.status_code == 200
        assert delete.json()["data"]["deleted"] == plan_id

        get_resp = client.get(f"/api/v1/plans/{plan_id}")
        assert get_resp.status_code == 404

    def test_create_empty_steps_rejected(self, client, auth_headers):
        # Init 至少一个 enabled step 是 ADR §2 的不变量
        payload = {"name": _uniq("bad"), "steps": []}
        resp = client.post("/api/v1/plans", json=payload, headers=auth_headers)
        assert resp.status_code == 422, resp.text

    def test_create_rejects_legacy_lifecycle_field(self, client, auth_headers, sample_script):
        # ADR-0020 §2 收口：plan.lifecycle 已删除，请求体携带该字段应被 Pydantic 拒绝。
        payload = {
            "name": _uniq("legacy"),
            "lifecycle": {"init": [], "teardown": []},
            "steps": _minimal_steps(),
        }
        resp = client.post("/api/v1/plans", json=payload, headers=auth_headers)
        assert resp.status_code == 422

    def test_next_plan_self_reference_rejected(self, client, auth_headers, sample_script):
        name = _uniq("self")
        create = client.post("/api/v1/plans", json={
            "name": name, "steps": _minimal_steps(),
        }, headers=auth_headers)
        plan_id = create.json()["data"]["id"]
        resp = client.put(f"/api/v1/plans/{plan_id}", json={
            "next_plan_id": plan_id,
        }, headers=auth_headers)
        assert resp.status_code == 422

    def test_create_rejects_missing_script_reference(self, client, auth_headers):
        payload = {
            "name": _uniq("missing_script"),
            "steps": [
                {"step_key": "missing", "script_name": "missing_script",
                 "script_version": "9.9.9", "stage": "init", "sort_order": 0,
                 "timeout_seconds": 30},
            ],
        }
        resp = client.post("/api/v1/plans", json=payload, headers=auth_headers)
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

    def test_run_rejects_failure_threshold_override(self, client, auth_headers):
        resp = client.post("/api/v1/plans/1/run", json={
            "device_ids": [1],
            "failure_threshold": 0.9,
        }, headers=auth_headers)
        assert resp.status_code == 422
