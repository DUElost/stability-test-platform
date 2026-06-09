"""Plan CRUD + dispatch API tests — ADR-0020."""

from uuid import uuid4

from backend.models.plan_run import PlanRun


def _uniq(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:8]}"


def _minimal_steps() -> list[dict]:
    return [
        {"step_key": "init_0", "script_name": "check_device",
         "script_version": "1.0.0", "stage": "init", "sort_order": 0,
         "timeout_seconds": 30},
    ]


def _ensure_legacy_aee_scripts(db_session) -> None:
    from backend.models.script import Script

    scripts = [
        ("scan_aee", "1.0.0"),
        ("export_mobilelogs", "1.0.0"),
    ]
    for name, version in scripts:
        existing = db_session.query(Script).filter(
            Script.name == name, Script.version == version
        ).first()
        if existing:
            continue
        db_session.add(Script(
            name=name,
            script_type="python",
            version=version,
            nfs_path=f"/nfs/scripts/{name}/{version}",
            content_sha256="1" * 64,
            is_active=True,
            default_params={},
            param_schema={},
        ))
    db_session.commit()


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

        get_resp = client.get(f"/api/v1/plans/{data['id']}", headers=auth_headers)
        assert get_resp.status_code == 200
        assert get_resp.json()["data"]["name"] == name

    def test_list_plans(self, client, auth_headers, sample_script):
        name = _uniq("plan")
        client.post("/api/v1/plans", json={
            "name": name, "steps": _minimal_steps(),
        }, headers=auth_headers)

        resp = client.get("/api/v1/plans", headers=auth_headers)
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

        get_resp = client.get(f"/api/v1/plans/{plan_id}", headers=auth_headers)
        assert get_resp.status_code == 404

    def test_delete_plan_with_historical_runs_returns_409(
        self, client, auth_headers, sample_script, db_session,
    ):
        name = _uniq("plan_hist")
        create = client.post("/api/v1/plans", json={
            "name": name, "steps": _minimal_steps(),
        }, headers=auth_headers)
        plan_id = create.json()["data"]["id"]

        db_session.add(PlanRun(
            plan_id=plan_id,
            status="FAILED",
            failure_threshold=0.05,
            plan_snapshot={"name": name, "plan_id": plan_id},
            run_type="MANUAL",
        ))
        db_session.commit()

        delete = client.delete(f"/api/v1/plans/{plan_id}", headers=auth_headers)
        assert delete.status_code == 409, delete.text
        assert "execution record" in delete.json()["detail"]

        get_resp = client.get(f"/api/v1/plans/{plan_id}", headers=auth_headers)
        assert get_resp.status_code == 200

    def test_update_plan_rejected_for_non_owner(self, client, auth_headers, sample_script):
        # 审计 #8: plans.py update/delete 必须拒绝非 owner 非 admin。
        from backend.core.security import create_access_token
        from backend.models.user import User
        from backend.tests.conftest import db_session  # type: ignore[attr-defined]

        name = _uniq("plan")
        create = client.post("/api/v1/plans", json={
            "name": name, "steps": _minimal_steps(),
        }, headers=auth_headers)
        plan_id = create.json()["data"]["id"]

        other_token = create_access_token(data={"sub": "otheruser", "role": "user"})
        other_headers = {"Authorization": f"Bearer {other_token}"}

        # 准备 otheruser 用户记录
        from backend.core.database import SessionLocal
        with SessionLocal() as s:
            if not s.query(User).filter(User.username == "otheruser").first():
                from backend.core.security import get_password_hash
                s.add(User(
                    username="otheruser",
                    hashed_password=get_password_hash("x"),
                    role="user",
                    is_active="Y",
                ))
                s.commit()

        update = client.put(f"/api/v1/plans/{plan_id}", json={
            "name": f"{name}_hack",
        }, headers=other_headers)
        assert update.status_code == 403

        delete = client.delete(f"/api/v1/plans/{plan_id}", headers=other_headers)
        assert delete.status_code == 403

    def test_admin_can_modify_other_users_plan(self, client, auth_headers, admin_headers, sample_script):
        # admin 应该可以修改任何用户的 Plan
        name = _uniq("plan")
        create = client.post("/api/v1/plans", json={
            "name": name, "steps": _minimal_steps(),
        }, headers=auth_headers)
        plan_id = create.json()["data"]["id"]

        update = client.put(f"/api/v1/plans/{plan_id}", json={
            "name": f"{name}_admin_renamed",
        }, headers=admin_headers)
        assert update.status_code == 200, update.text

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

    def test_create_rejects_legacy_aee_scripts_for_new_plan(
        self, client, auth_headers, sample_script, db_session,
    ):
        _ensure_legacy_aee_scripts(db_session)
        payload = {
            "name": _uniq("legacy_aee_create"),
            "steps": _minimal_steps() + [
                {"step_key": "scan", "script_name": "scan_aee",
                 "script_version": "1.0.0", "stage": "patrol", "sort_order": 0,
                 "timeout_seconds": 30},
            ],
        }

        resp = client.post("/api/v1/plans", json=payload, headers=auth_headers)

        assert resp.status_code == 422, resp.text
        assert resp.json()["detail"] == {
            "code": "LEGACY_AEE_SCRIPTS_DISABLED",
            "scripts": ["scan_aee:1.0.0"],
        }

    def test_update_rejects_legacy_aee_scripts_for_existing_plan(
        self, client, auth_headers, sample_script, db_session,
    ):
        _ensure_legacy_aee_scripts(db_session)
        name = _uniq("legacy_aee_update")
        create = client.post("/api/v1/plans", json={
            "name": name, "steps": _minimal_steps(),
        }, headers=auth_headers)
        plan_id = create.json()["data"]["id"]

        resp = client.put(f"/api/v1/plans/{plan_id}", json={
            "steps": _minimal_steps() + [
                {"step_key": "export", "script_name": "export_mobilelogs",
                 "script_version": "1.0.0", "stage": "teardown", "sort_order": 0,
                 "timeout_seconds": 30},
            ],
        }, headers=auth_headers)

        assert resp.status_code == 422, resp.text
        assert resp.json()["detail"] == {
            "code": "LEGACY_AEE_SCRIPTS_DISABLED",
            "scripts": ["export_mobilelogs:1.0.0"],
        }


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


# ── ADR-0023 C1: fail-fast script availability gate ─────────────────────


class TestPlanDispatchFailFast:
    """ADR-0023 C1:Plan 创建后引用脚本被失活,/run 与 /run/preview 必须返回
    400 + 统一 ``{code: INVALID_SCRIPT_REFS, missing: [...]}`` 形状。"""

    @staticmethod
    def _create_plan(client, auth_headers) -> int:
        name = _uniq("ff")
        resp = client.post("/api/v1/plans", json={
            "name": name, "steps": _minimal_steps(),
        }, headers=auth_headers)
        assert resp.status_code == 201, resp.text
        return resp.json()["data"]["id"]

    @staticmethod
    def _deactivate_check_device(db_session) -> None:
        from backend.models.script import Script
        rows = db_session.query(Script).filter(
            Script.name == "check_device", Script.version == "1.0.0",
        ).all()
        for r in rows:
            r.is_active = False
        db_session.commit()

    def test_preview_returns_400_invalid_script_refs(
        self, client, auth_headers, db_session, sample_script, sample_device,
    ):
        plan_id = self._create_plan(client, auth_headers)
        self._deactivate_check_device(db_session)

        resp = client.post(
            f"/api/v1/plans/{plan_id}/run/preview",
            json={"device_ids": [sample_device.id]},
            headers=auth_headers,
        )
        assert resp.status_code == 400, resp.text
        detail = resp.json()["detail"]
        assert detail["code"] == "INVALID_SCRIPT_REFS"
        assert detail["missing"] == ["check_device:1.0.0"]

    def test_run_returns_400_invalid_script_refs_no_plan_run(
        self, client, auth_headers, db_session, sample_script, sample_device,
    ):
        plan_id = self._create_plan(client, auth_headers)
        self._deactivate_check_device(db_session)

        resp = client.post(
            f"/api/v1/plans/{plan_id}/run",
            json={"device_ids": [sample_device.id]},
            headers=auth_headers,
        )
        assert resp.status_code == 400, resp.text
        detail = resp.json()["detail"]
        assert detail["code"] == "INVALID_SCRIPT_REFS"
        assert detail["missing"] == ["check_device:1.0.0"]

        # fail-fast 阶段 1:必须在 INSERT 之前拒绝,无 PlanRun 行落库
        from backend.models.plan_run import PlanRun
        assert db_session.query(PlanRun).filter(
            PlanRun.plan_id == plan_id
        ).count() == 0

    @staticmethod
    def _insert_legacy_plan(db_session) -> int:
        from backend.models.plan import Plan, PlanStep

        plan = Plan(
            name=_uniq("legacy_plan"),
            description="legacy aee plan",
            failure_threshold=0.05,
            created_by="testuser",
        )
        db_session.add(plan)
        db_session.flush()
        db_session.add_all([
            PlanStep(
                plan_id=plan.id,
                step_key="init_0",
                script_name="check_device",
                script_version="1.0.0",
                stage="init",
                sort_order=0,
                timeout_seconds=30,
                retry=0,
                enabled=True,
            ),
            PlanStep(
                plan_id=plan.id,
                step_key="scan",
                script_name="scan_aee",
                script_version="1.0.0",
                stage="patrol",
                sort_order=0,
                timeout_seconds=30,
                retry=0,
                enabled=True,
            ),
        ])
        db_session.commit()
        return plan.id

    def test_preview_rejects_existing_legacy_aee_plan(
        self, client, auth_headers, db_session, sample_script, sample_device,
    ):
        _ensure_legacy_aee_scripts(db_session)
        plan_id = self._insert_legacy_plan(db_session)

        resp = client.post(
            f"/api/v1/plans/{plan_id}/run/preview",
            json={"device_ids": [sample_device.id]},
            headers=auth_headers,
        )

        assert resp.status_code == 400, resp.text
        assert resp.json()["detail"] == {
            "code": "LEGACY_AEE_SCRIPTS_DISABLED",
            "scripts": ["scan_aee:1.0.0"],
        }

    def test_run_rejects_existing_legacy_aee_plan_without_plan_run(
        self, client, auth_headers, db_session, sample_script, sample_device,
    ):
        _ensure_legacy_aee_scripts(db_session)
        plan_id = self._insert_legacy_plan(db_session)

        resp = client.post(
            f"/api/v1/plans/{plan_id}/run",
            json={"device_ids": [sample_device.id]},
            headers=auth_headers,
        )

        assert resp.status_code == 400, resp.text
        assert resp.json()["detail"] == {
            "code": "LEGACY_AEE_SCRIPTS_DISABLED",
            "scripts": ["scan_aee:1.0.0"],
        }
        assert db_session.query(PlanRun).filter(PlanRun.plan_id == plan_id).count() == 0
