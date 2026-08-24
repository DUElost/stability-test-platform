"""Plan CRUD + dispatch API tests — ADR-0020."""

import threading

import pytest
from uuid import uuid4

from fastapi import HTTPException

from backend.api.routes.plans import _validate_plan_dag
from backend.core.database import SessionLocal
from backend.models.plan import Plan
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

    def test_list_plans_hides_existing_legacy_aee_plan(
        self, client, auth_headers, sample_script, db_session,
    ):
        _ensure_legacy_aee_scripts(db_session)
        legacy_plan_id = TestPlanDispatchFailFast._insert_legacy_plan(db_session)

        resp = client.get("/api/v1/plans", headers=auth_headers)

        assert resp.status_code == 200
        plan_ids = {item["id"] for item in resp.json()["data"]}
        assert legacy_plan_id not in plan_ids

    def test_update_plan(self, client, auth_headers, sample_script):
        name = _uniq("plan")
        create = client.post("/api/v1/plans", json={
            "name": name, "steps": _minimal_steps(),
        }, headers=auth_headers)
        plan_id = create.json()["data"]["id"]

        update = client.put(f"/api/v1/plans/{plan_id}", json={
            "name": f"{name}_updated",
            "expected_updated_at": create.json()["data"]["updated_at"],
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

    def test_update_plan_optimistic_lock_409(self, client, auth_headers, sample_script):
        """#268 多Worker B3:携带过期 expected_updated_at 的保存必须 409。"""
        name = _uniq("plan")
        create = client.post("/api/v1/plans", json={
            "name": name, "steps": _minimal_steps(),
        }, headers=auth_headers)
        plan = create.json()["data"]
        plan_id = plan["id"]
        loaded_at = plan["updated_at"]

        # 先做一次合法更新,把 updated_at 推走
        first = client.put(f"/api/v1/plans/{plan_id}", json={
            "name": f"{name}_v1", "expected_updated_at": loaded_at,
        }, headers=auth_headers)
        assert first.status_code == 200
        stale = client.put(f"/api/v1/plans/{plan_id}", json={
            "name": f"{name}_v2", "expected_updated_at": loaded_at,
        }, headers=auth_headers)
        assert stale.status_code == 409
        assert "modified by another session" in stale.json()["detail"]

        # 用最新 updated_at 可正常保存
        fresh_at = first.json()["data"]["updated_at"]
        ok_resp = client.put(f"/api/v1/plans/{plan_id}", json={
            "name": f"{name}_v2", "expected_updated_at": fresh_at,
        }, headers=auth_headers)
        assert ok_resp.status_code == 200
        assert ok_resp.json()["data"]["name"] == f"{name}_v2"

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

    def test_get_plan_hides_existing_legacy_aee_plan(
        self, client, auth_headers, sample_script, db_session,
    ):
        _ensure_legacy_aee_scripts(db_session)
        legacy_plan_id = TestPlanDispatchFailFast._insert_legacy_plan(db_session)

        resp = client.get(f"/api/v1/plans/{legacy_plan_id}", headers=auth_headers)

        assert resp.status_code == 404, resp.text

    def test_update_plan_hides_existing_legacy_aee_plan(
        self, client, auth_headers, sample_script, db_session,
    ):
        _ensure_legacy_aee_scripts(db_session)
        legacy_plan_id = TestPlanDispatchFailFast._insert_legacy_plan(db_session)

        resp = client.put(
            f"/api/v1/plans/{legacy_plan_id}",
            json={"name": "legacy_hidden"},
            headers=auth_headers,
        )

        assert resp.status_code == 404, resp.text

    def test_delete_plan_hides_existing_legacy_aee_plan(
        self, client, auth_headers, sample_script, db_session,
    ):
        _ensure_legacy_aee_scripts(db_session)
        legacy_plan_id = TestPlanDispatchFailFast._insert_legacy_plan(db_session)

        resp = client.delete(f"/api/v1/plans/{legacy_plan_id}", headers=auth_headers)

        assert resp.status_code == 404, resp.text

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

    def test_delete_plan_with_stale_version_returns_409(self, client, auth_headers, sample_script):
        """#281 P2:删除支持 expected_updated_at 版本校验——旧页面不能删除
        已被其他客户端修改的 Plan;令牌可省略(兼容旧客户端)。"""
        name = _uniq("plan_ver")
        create = client.post("/api/v1/plans", json={
            "name": name, "steps": _minimal_steps(),
        }, headers=auth_headers)
        plan_id = create.json()["data"]["id"]

        stale = client.delete(
            f"/api/v1/plans/{plan_id}",
            params={"expected_updated_at": "2000-01-01T00:00:00Z"},
            headers=auth_headers,
        )
        assert stale.status_code == 409, stale.text

        get_resp = client.get(f"/api/v1/plans/{plan_id}", headers=auth_headers)
        assert get_resp.status_code == 200  # 版本冲突不删除

        fresh = create.json()["data"]["updated_at"]
        ok = client.delete(
            f"/api/v1/plans/{plan_id}",
            params={"expected_updated_at": fresh},
            headers=auth_headers,
        )
        assert ok.status_code == 200, ok.text

    def test_update_plan_rejected_for_non_owner(self, client, auth_headers, sample_script):
        # 审计 #8: plans.py update/delete 必须拒绝非 owner 非 admin。
        from backend.core.security import create_access_token
        from backend.models.user import User

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
            "expected_updated_at": create.json()["data"]["updated_at"],
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
            "expected_updated_at": create.json()["data"]["updated_at"],
        }, headers=auth_headers)
        assert resp.status_code == 422

    def test_postgresql_concurrent_plan_links_cannot_create_cycle(
        self, db_session,
    ):
        plan_a = Plan(name=_uniq("dag_a"))
        plan_b = Plan(name=_uniq("dag_b"))
        db_session.add_all([plan_a, plan_b])
        db_session.commit()
        plan_ids = (plan_a.id, plan_b.id)
        barrier = threading.Barrier(2)
        results: list[str] = []
        errors: list[Exception] = []

        def link(source_id: int, target_id: int):
            db = SessionLocal()
            try:
                barrier.wait(timeout=5)
                _validate_plan_dag(db, source_id, target_id)
                source = db.get(Plan, source_id)
                source.next_plan_id = target_id
                db.commit()
                results.append("linked")
            except HTTPException as exc:
                db.rollback()
                assert exc.status_code == 422
                results.append("cycle_rejected")
            except Exception as exc:
                db.rollback()
                errors.append(exc)
            finally:
                db.close()

        threads = [
            threading.Thread(target=link, args=(plan_ids[0], plan_ids[1])),
            threading.Thread(target=link, args=(plan_ids[1], plan_ids[0])),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        assert all(not thread.is_alive() for thread in threads)
        assert errors == []
        assert sorted(results) == ["cycle_rejected", "linked"]
        db_session.expire_all()
        persisted_a = db_session.get(Plan, plan_ids[0])
        persisted_b = db_session.get(Plan, plan_ids[1])
        assert not (
            persisted_a.next_plan_id == persisted_b.id
            and persisted_b.next_plan_id == persisted_a.id
        )

    def test_create_rejects_hidden_legacy_next_plan_reference(
        self, client, auth_headers, sample_script, db_session,
    ):
        _ensure_legacy_aee_scripts(db_session)
        legacy_plan_id = TestPlanDispatchFailFast._insert_legacy_plan(db_session)

        resp = client.post("/api/v1/plans", json={
            "name": _uniq("next_hidden_create"),
            "next_plan_id": legacy_plan_id,
            "steps": _minimal_steps(),
        }, headers=auth_headers)

        assert resp.status_code == 404, resp.text
        assert resp.json()["detail"] == f"next_plan_id {legacy_plan_id} not found"

    def test_update_rejects_hidden_legacy_next_plan_reference(
        self, client, auth_headers, sample_script, db_session,
    ):
        _ensure_legacy_aee_scripts(db_session)
        legacy_plan_id = TestPlanDispatchFailFast._insert_legacy_plan(db_session)
        create = client.post("/api/v1/plans", json={
            "name": _uniq("next_hidden_update"),
            "steps": _minimal_steps(),
        }, headers=auth_headers)
        plan_id = create.json()["data"]["id"]

        resp = client.put(f"/api/v1/plans/{plan_id}", json={
            "next_plan_id": legacy_plan_id,
            "expected_updated_at": create.json()["data"]["updated_at"],
        }, headers=auth_headers)

        assert resp.status_code == 404, resp.text
        assert resp.json()["detail"] == f"next_plan_id {legacy_plan_id} not found"

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
            "expected_updated_at": create.json()["data"]["updated_at"],
        }, headers=auth_headers)

        assert resp.status_code == 422, resp.text
        assert resp.json()["detail"] == {
            "code": "LEGACY_AEE_SCRIPTS_DISABLED",
            "scripts": ["export_mobilelogs:1.0.0"],
        }


class TestPlanAttribution:
    """ADR-0029（#405）：create/update 写入 project/specialty，字典 API 可读。"""

    @pytest.fixture
    def _project(self, db_session):
        from backend.models.project import TestProject

        proj = TestProject(project_key=_uniq("PRJ"), display_name="attribution")
        db_session.add(proj)
        db_session.commit()
        return proj

    @pytest.fixture
    def _specialty(self, db_session):
        from backend.models.project import Specialty

        spec = Specialty(key="mtbf", display_name="MTBF", sort_order=1)
        db_session.add(spec)
        db_session.commit()
        return spec

    def test_create_with_project_and_specialty(
        self, client, auth_headers, sample_script, _project, _specialty,
    ):
        resp = client.post("/api/v1/plans", json={
            "name": _uniq("plan"), "steps": _minimal_steps(),
            "project_key": _project.project_key,
            "specialty_key": "mtbf",
        }, headers=auth_headers)
        assert resp.status_code == 201, resp.text
        data = resp.json()["data"]
        assert data["project_key"] == _project.project_key
        assert data["specialty_key"] == "mtbf"

    def test_create_unknown_keys_404(self, client, auth_headers, sample_script):
        for extra in ({"project_key": "nope"}, {"specialty_key": "nope"}):
            resp = client.post("/api/v1/plans", json={
                "name": _uniq("plan"), "steps": _minimal_steps(), **extra,
            }, headers=auth_headers)
            assert resp.status_code == 404, extra

    def test_update_changes_and_clears_attribution(
        self, client, auth_headers, sample_script, db_session, _project, _specialty,
    ):
        from backend.models.project import TestProject

        other = TestProject(project_key=_uniq("PRJ2"), display_name="other")
        db_session.add(other)
        db_session.commit()

        create = client.post("/api/v1/plans", json={
            "name": _uniq("plan"), "steps": _minimal_steps(),
            "project_key": _project.project_key, "specialty_key": "mtbf",
        }, headers=auth_headers)
        plan_id = create.json()["data"]["id"]

        # 只改 project：specialty 不受影响（fields_set 语义）
        upd = client.put(f"/api/v1/plans/{plan_id}", json={
            "project_key": other.project_key,
            "expected_updated_at": create.json()["data"]["updated_at"],
        }, headers=auth_headers)
        assert upd.status_code == 200, upd.text
        data = upd.json()["data"]
        assert data["project_key"] == other.project_key
        assert data["specialty_key"] == "mtbf"

        # 显式 null = 清除
        upd2 = client.put(f"/api/v1/plans/{plan_id}", json={
            "project_key": None, "specialty_key": None,
            "expected_updated_at": data["updated_at"],
        }, headers=auth_headers)
        assert upd2.status_code == 200, upd2.text
        assert upd2.json()["data"]["project_key"] is None
        assert upd2.json()["data"]["specialty_key"] is None

    def test_specialties_dictionary_endpoint(
        self, client, auth_headers, _specialty,
    ):
        resp = client.get("/api/v1/specialties", headers=auth_headers)
        assert resp.status_code == 200
        rows = resp.json()["data"]
        match = [r for r in rows if r["key"] == "mtbf"]
        assert match and match[0]["display_name"] == "MTBF"

    def test_suite_binding_lifecycle(
        self, client, auth_headers, sample_script, db_session,
    ):
        """ADR-0030 v1.4（#404 PR-B）：suite_name 绑定/改绑/解绑与输出。"""
        from backend.models.suite import TestSuite

        s1 = TestSuite(name="MTBF-bind-1", root_config={})
        s2 = TestSuite(name="MTBF-bind-2", root_config={})
        db_session.add_all([s1, s2])
        db_session.commit()

        create = client.post("/api/v1/plans", json={
            "name": _uniq("plan"), "steps": _minimal_steps(),
            "suite_name": s1.name,
        }, headers=auth_headers)
        assert create.status_code == 201, create.text
        assert create.json()["data"]["suite_name"] == s1.name

        # 改绑
        upd = client.put(f"/api/v1/plans/{create.json()['data']['id']}", json={
            "suite_name": s2.name,
            "expected_updated_at": create.json()["data"]["updated_at"],
        }, headers=auth_headers)
        assert upd.status_code == 200, upd.text
        assert upd.json()["data"]["suite_name"] == s2.name

        # 显式 null 解绑 → 回到 P0 文件真源模式
        upd2 = client.put(f"/api/v1/plans/{create.json()['data']['id']}", json={
            "suite_name": None,
            "expected_updated_at": upd.json()["data"]["updated_at"],
        }, headers=auth_headers)
        assert upd2.status_code == 200, upd2.text
        assert upd2.json()["data"]["suite_name"] is None

    def test_create_unknown_suite_404(self, client, auth_headers, sample_script):
        resp = client.post("/api/v1/plans", json={
            "name": _uniq("plan"), "steps": _minimal_steps(),
            "suite_name": "no-such-suite",
        }, headers=auth_headers)
        assert resp.status_code == 404


class TestAppendChainTail:
    """#281 P1:原子链尾追加——单事务内锁定链尾、校验版本、创建新 Plan、
    更新 next_plan_id;冲突整体回滚,不产生孤立 Plan。"""

    def _create_plan(self, client, auth_headers) -> dict:
        name = _uniq("chain")
        resp = client.post("/api/v1/plans", json={
            "name": name, "steps": _minimal_steps(),
        }, headers=auth_headers)
        assert resp.status_code == 201, resp.text
        return resp.json()["data"]

    def _link(self, client, auth_headers, head: dict, tail: dict) -> None:
        resp = client.put(f"/api/v1/plans/{head['id']}", json={
            "next_plan_id": tail["id"],
            "expected_updated_at": head["updated_at"],
        }, headers=auth_headers)
        assert resp.status_code == 200, resp.text

    def test_append_links_tail_and_returns_new_plan(self, client, auth_headers, sample_script):
        head = self._create_plan(client, auth_headers)
        tail = self._create_plan(client, auth_headers)
        self._link(client, auth_headers, head, tail)

        resp = client.post(
            f"/api/v1/plans/{head['id']}/append-chain-tail",
            json={
                "name": _uniq("newtail"), "steps": _minimal_steps(),
                "expected_updated_at": tail["updated_at"],
            },
            headers=auth_headers,
        )
        assert resp.status_code == 201, resp.text
        new_plan = resp.json()["data"]
        assert new_plan["next_plan_id"] is None

        fresh_tail = client.get(f"/api/v1/plans/{tail['id']}", headers=auth_headers).json()["data"]
        assert fresh_tail["next_plan_id"] == new_plan["id"]
        # 中间节点不被改动
        fresh_head = client.get(f"/api/v1/plans/{head['id']}", headers=auth_headers).json()["data"]
        assert fresh_head["next_plan_id"] == tail["id"]

    def test_append_walks_to_real_tail_beyond_anchor(self, client, auth_headers, sample_script):
        """从链中间追加时,新 Plan 接在真正链尾之后,而不是接在锚点之后。"""
        anchor = self._create_plan(client, auth_headers)
        tail = self._create_plan(client, auth_headers)
        self._link(client, auth_headers, anchor, tail)

        resp = client.post(
            f"/api/v1/plans/{anchor['id']}/append-chain-tail",
            json={"name": _uniq("real_tail"), "steps": _minimal_steps()},
            headers=auth_headers,
        )
        assert resp.status_code == 201, resp.text
        new_plan = resp.json()["data"]

        fresh_tail = client.get(f"/api/v1/plans/{tail['id']}", headers=auth_headers).json()["data"]
        assert fresh_tail["next_plan_id"] == new_plan["id"]
        fresh_anchor = client.get(f"/api/v1/plans/{anchor['id']}", headers=auth_headers).json()["data"]
        assert fresh_anchor["next_plan_id"] == tail["id"]

    def test_append_stale_token_409_rolls_back_entirely(self, client, auth_headers, sample_script):
        """版本令牌过期 → 409 且整体回滚:新 Plan 不落库(不再产生孤立 Plan)。"""
        head = self._create_plan(client, auth_headers)

        resp = client.post(
            f"/api/v1/plans/{head['id']}/append-chain-tail",
            json={
                "name": _uniq("orphan"), "steps": _minimal_steps(),
                "expected_updated_at": "2000-01-01T00:00:00Z",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 409, resp.text

        plans = client.get("/api/v1/plans?skip=0&limit=200", headers=auth_headers).json()["data"]
        assert not any(p["name"].startswith("orphan_") for p in plans)
        fresh_head = client.get(f"/api/v1/plans/{head['id']}", headers=auth_headers).json()["data"]
        assert fresh_head["next_plan_id"] is None

    def test_append_missing_plan_404(self, client, auth_headers):
        resp = client.post(
            "/api/v1/plans/999999/append-chain-tail",
            json={"name": _uniq("ghost"), "steps": _minimal_steps()},
            headers=auth_headers,
        )
        assert resp.status_code == 404, resp.text


class TestAppendChainTailConcurrent:
    """#281 二轮 P1:并发链尾追加(仅 PostgreSQL 有意义——SQLite 跳过
    FOR UPDATE,单线程也测不到锁后重读路径)。

    复现场景:两个客户端同时读到同一条旧链尾;先到者加锁、创建新 Plan、
    提交;后到者拿到锁后若仍信任加锁前的 updated_at,乐观锁会误判通过,
    把先到者刚连上的新 Plan 覆盖成孤立记录。
    """

    def _setup_head(self, db_session) -> tuple[Plan, int]:
        """创建链首 Plan 与真实 admin 用户(审计 user_id 有 FK,不能伪造)。"""
        from backend.models.user import User as UserModel

        admin = UserModel(
            username=_uniq("conc_admin"), hashed_password="unused",
            role="admin", is_active="Y",
        )
        plan = Plan(name=_uniq("conc_head"))
        db_session.add_all([admin, plan])
        db_session.commit()
        db_session.refresh(plan)
        return plan, admin.id

    @staticmethod
    def _admin_user(admin_id: int):
        from backend.models.user import User

        return User(id=admin_id, username="conc-admin", role="admin", is_active="Y")

    @classmethod
    def _append(cls, anchor_id: int, token, name: str, admin_id: int) -> tuple[int, object]:
        from backend.api.routes.plans import PlanChainTailCreate, append_chain_tail

        db = SessionLocal()
        try:
            result = append_chain_tail(
                anchor_id,
                PlanChainTailCreate(
                    name=name, steps=_minimal_steps(), expected_updated_at=token,
                ),
                request=None,
                db=db,
                current_user=cls._admin_user(admin_id),
            )
            db.commit()
            return 201, result
        except HTTPException as exc:
            db.rollback()
            return exc.status_code, None
        finally:
            db.close()

    @staticmethod
    def _chain_ids(db_session, head_id: int) -> list[int]:
        """沿 next_plan_id 收集整条链(带环保护)。"""
        ids: list[int] = []
        seen: set[int] = set()
        cur = db_session.get(Plan, head_id)
        while cur is not None and cur.id not in seen:
            seen.add(cur.id)
            ids.append(cur.id)
            if cur.next_plan_id is None:
                break
            nxt = db_session.get(Plan, cur.next_plan_id)
            if nxt is None:
                break
            cur = nxt
        return ids

    def test_concurrent_append_stale_token_one_409_no_orphan(
        self, db_session, sample_script,
    ):
        """两个客户端持同一份旧令牌并发追加:一个 201、一个 409;
        失败方整体回滚,链上无孤立节点。"""
        head, admin_id = self._setup_head(db_session)
        token = head.updated_at
        barrier = threading.Barrier(2)
        outcomes: list[tuple[int, str]] = []
        errors: list[Exception] = []

        def worker() -> None:
            try:
                barrier.wait(timeout=5)
                name = _uniq("conc_stale")
                status, _ = self._append(head.id, token, name, admin_id)
                outcomes.append((status, name))
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=20)

        assert not any(t.is_alive() for t in threads)
        assert errors == []
        statuses = sorted(s for s, _ in outcomes)
        assert statuses == [201, 409], outcomes

        loser = next(name for s, name in outcomes if s == 409)
        winner = next(name for s, name in outcomes if s == 201)
        db_session.expire_all()
        # 只有赢家落库(失败方整体回滚),链 = head → 唯一新 Plan,无孤立
        assert db_session.query(Plan).filter(Plan.name == loser).count() == 0
        chain = self._chain_ids(db_session, head.id)
        assert len(chain) == 2
        winner_id = db_session.query(Plan).filter(Plan.name == winner).one().id
        assert chain[1] == winner_id

    def test_concurrent_append_without_token_both_land_no_orphan(
        self, db_session, sample_script,
    ):
        """省略令牌(前端链尾超窗的正式行为)并发追加:两个都成功且按序
        连在真实链尾之后,不覆盖、不产生孤立 Plan。"""
        head, admin_id = self._setup_head(db_session)
        barrier = threading.Barrier(2)
        outcomes: list[tuple[int, str]] = []
        errors: list[Exception] = []

        def worker() -> None:
            try:
                barrier.wait(timeout=5)
                name = _uniq("conc_notoken")
                status, _ = self._append(head.id, None, name, admin_id)
                outcomes.append((status, name))
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=20)

        assert not any(t.is_alive() for t in threads)
        assert errors == []
        assert sorted(s for s, _ in outcomes) == [201, 201], outcomes

        db_session.expire_all()
        chain = self._chain_ids(db_session, head.id)
        assert len(chain) == 3  # head + 两个新 Plan,全部可达
        created = {
            p.id for p in db_session.query(Plan).filter(Plan.name.like("conc_notoken%")).all()
        }
        assert len(created) == 2
        assert set(chain[1:]) == created  # 两个新 Plan 都在链上,无孤立


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


# ── 执行前可选 WiFi（资源池方案）────────────────────────────────────────


class TestPlanRunWifiChoice:
    """WiFi 连接是**执行时**的选择：不传 = 不连接；传 pool_id = 用该网络。

    校验放在路由层而不是等准入泵：否则选错网络要等 PlanRun 已经 QUEUED
    之后才以 AllocationError 暴露，操作员看到的是一个失败的 run 而不是一次
    被拒绝的提交。
    """

    @staticmethod
    def _create_plan(client, auth_headers) -> int:
        resp = client.post("/api/v1/plans", json={
            "name": _uniq("wifi"), "steps": _minimal_steps(),
        }, headers=auth_headers)
        assert resp.status_code == 201, resp.text
        return resp.json()["data"]["id"]

    @staticmethod
    def _create_monkey_setup_plan(client, auth_headers, db_session) -> int:
        from backend.models.script import Script

        db_session.add(Script(
            name="monkey_setup",
            display_name="monkey_setup",
            category="device",
            script_type="python",
            version="2.0.0",
            nfs_path="/s/monkey_setup/v2.0.0/monkey_setup.py",
            content_sha256="a" * 64,
            param_schema={},
            default_params={},
            is_active=True,
        ))
        db_session.commit()
        resp = client.post("/api/v1/plans", json={
            "name": _uniq("wifi-ms"),
            "steps": [{
                "step_key": "init_0",
                "script_name": "monkey_setup",
                "script_version": "2.0.0",
                "stage": "init",
                "sort_order": 0,
                "timeout_seconds": 300,
            }],
        }, headers=auth_headers)
        assert resp.status_code == 201, resp.text
        return resp.json()["data"]["id"]

    @staticmethod
    def _make_pool(db_session, *, resource_type="wifi", is_active=True):
        from backend.models.resource_pool import ResourcePool
        pool = ResourcePool(
            name=_uniq("pool"), resource_type=resource_type,
            config={"ssid": "office-5G", "password": "pw"},
            max_concurrent_devices=50, is_active=is_active,
        )
        db_session.add(pool)
        db_session.commit()
        return pool

    def test_omitting_wifi_pool_id_leaves_run_context_clean(
        self, client, auth_headers, db_session, sample_script, sample_device,
    ):
        plan_id = self._create_plan(client, auth_headers)
        resp = client.post(
            f"/api/v1/plans/{plan_id}/run",
            json={"device_ids": [sample_device.id]},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        assert "wifi_pool_id" not in (resp.json()["data"]["run_context"] or {})

    def test_valid_pool_is_recorded_in_run_context(
        self, client, auth_headers, db_session, sample_script, sample_device,
    ):
        plan_id = self._create_monkey_setup_plan(client, auth_headers, db_session)
        pool = self._make_pool(db_session)
        resp = client.post(
            f"/api/v1/plans/{plan_id}/run",
            json={"device_ids": [sample_device.id], "wifi_pool_id": pool.id},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["run_context"]["wifi_pool_id"] == pool.id

    def test_wifi_pool_rejected_when_plan_has_no_wifi_consumer(
        self, client, auth_headers, db_session, sample_script, sample_device,
    ):
        plan_id = self._create_plan(client, auth_headers)
        pool = self._make_pool(db_session)
        resp = client.post(
            f"/api/v1/plans/{plan_id}/run",
            json={"device_ids": [sample_device.id], "wifi_pool_id": pool.id},
            headers=auth_headers,
        )
        assert resp.status_code == 400, resp.text
        assert "wifi_pool_id requires" in resp.json()["detail"]

    def test_preview_rejects_wifi_pool_without_consumer(
        self, client, auth_headers, db_session, sample_script, sample_device,
    ):
        plan_id = self._create_plan(client, auth_headers)
        pool = self._make_pool(db_session)
        resp = client.post(
            f"/api/v1/plans/{plan_id}/run/preview",
            json={"device_ids": [sample_device.id], "wifi_pool_id": pool.id},
            headers=auth_headers,
        )
        assert resp.status_code == 400, resp.text
        assert "wifi_pool_id requires" in resp.json()["detail"]

    def test_unknown_pool_is_rejected_without_creating_a_plan_run(
        self, client, auth_headers, db_session, sample_script, sample_device,
    ):
        from backend.models.plan_run import PlanRun

        plan_id = self._create_plan(client, auth_headers)
        resp = client.post(
            f"/api/v1/plans/{plan_id}/run",
            json={"device_ids": [sample_device.id], "wifi_pool_id": 987654},
            headers=auth_headers,
        )
        assert resp.status_code == 400, resp.text
        assert "987654" in resp.json()["detail"]
        assert db_session.query(PlanRun).filter(PlanRun.plan_id == plan_id).count() == 0

    def test_inactive_pool_is_rejected(
        self, client, auth_headers, db_session, sample_script, sample_device,
    ):
        plan_id = self._create_plan(client, auth_headers)
        pool = self._make_pool(db_session, is_active=False)
        resp = client.post(
            f"/api/v1/plans/{plan_id}/run",
            json={"device_ids": [sample_device.id], "wifi_pool_id": pool.id},
            headers=auth_headers,
        )
        assert resp.status_code == 400, resp.text

    def test_non_wifi_pool_is_rejected(
        self, client, auth_headers, db_session, sample_script, sample_device,
    ):
        """别的资源类型的池不能拿来当 WiFi 用。"""
        plan_id = self._create_plan(client, auth_headers)
        pool = self._make_pool(db_session, resource_type="sim-card")
        resp = client.post(
            f"/api/v1/plans/{plan_id}/run",
            json={"device_ids": [sample_device.id], "wifi_pool_id": pool.id},
            headers=auth_headers,
        )
        assert resp.status_code == 400, resp.text

    def test_preview_rejects_invalid_pool_too(
        self, client, auth_headers, db_session, sample_script, sample_device,
    ):
        plan_id = self._create_plan(client, auth_headers)
        resp = client.post(
            f"/api/v1/plans/{plan_id}/run/preview",
            json={"device_ids": [sample_device.id], "wifi_pool_id": 987654},
            headers=auth_headers,
        )
        assert resp.status_code == 400, resp.text


def _ensure_script(
    db_session,
    name: str,
    version: str,
    *,
    capabilities=None,
) -> None:
    from backend.models.script import Script

    existing = db_session.query(Script).filter(
        Script.name == name, Script.version == version
    ).first()
    if existing:
        if capabilities is not None:
            existing.capabilities = capabilities
            db_session.commit()
        return
    db_session.add(Script(
        name=name,
        script_type="python",
        version=version,
        nfs_path=f"/nfs/scripts/{name}/{version}",
        content_sha256="2" * 64,
        capabilities=capabilities or [],
        is_active=True,
        default_params={},
        param_schema={},
    ))
    db_session.commit()


class TestStallRequiresProgressScript:
    """#136：stall_seconds>0 时脚本版本必须已知支持 PROGRESS。"""

    def test_create_rejects_stall_on_legacy_script(
        self, client, auth_headers, sample_script,
    ):
        steps = _minimal_steps()
        steps[0]["stall_seconds"] = 120
        resp = client.post("/api/v1/plans", json={
            "name": _uniq("plan"),
            "steps": steps,
        }, headers=auth_headers)
        assert resp.status_code == 422, resp.text
        detail = resp.json()["detail"]
        assert detail["code"] == "STALL_REQUIRES_PROGRESS_SCRIPT"
        assert detail["steps"] == ["check_device:1.0.0"]

    def test_create_accepts_stall_on_progress_capable_script(
        self, client, auth_headers, db_session,
    ):
        _ensure_script(
            db_session, "monkey_setup", "v2.3.3",
            capabilities=["progress_stamps"],
        )
        steps = [{
            "step_key": "init_0",
            "script_name": "monkey_setup",
            "script_version": "v2.3.3",
            "stage": "init",
            "sort_order": 0,
            "timeout_seconds": 300,
            "stall_seconds": 120,
        }]
        resp = client.post("/api/v1/plans", json={
            "name": _uniq("plan"),
            "steps": steps,
        }, headers=auth_headers)
        assert resp.status_code == 201, resp.text
        assert resp.json()["data"]["steps"][0]["stall_seconds"] == 120

    def test_update_rejects_stall_on_legacy_script(
        self, client, auth_headers, sample_script,
    ):
        create = client.post("/api/v1/plans", json={
            "name": _uniq("plan"),
            "steps": _minimal_steps(),
        }, headers=auth_headers)
        plan_id = create.json()["data"]["id"]

        steps = _minimal_steps()
        steps[0]["stall_seconds"] = 120
        resp = client.put(f"/api/v1/plans/{plan_id}", json={
            "steps": steps,
            "expected_updated_at": create.json()["data"]["updated_at"],
        }, headers=auth_headers)
        assert resp.status_code == 422, resp.text
        assert resp.json()["detail"]["code"] == "STALL_REQUIRES_PROGRESS_SCRIPT"
