"""Tests for schedules API routes — ADR-0020 (Plan-based)."""
import pytest
from datetime import datetime, timezone

from backend.models.plan import Plan
from backend.models.plan import PlanStep
from backend.models.schedule import TaskSchedule


class TestListSchedules:
    def test_list_schedules_empty(self, client, auth_headers):
        response = client.get("/api/v1/schedules", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0


class TestCreateSchedule:
    def test_create_schedule(self, client, auth_headers):
        """plan_id is required; task_name / task_type are gone."""
        response = client.post(
            "/api/v1/schedules",
            json={
                "name": "Daily Monkey",
                "cron_expr": "0 8 * * *",
                "task_name": "Monkey Test",
                "task_type": "MONKEY",
                "task_params": {"count": 5000},
                "enabled": True,
            },
            headers=auth_headers,
        )
        # Missing required plan_id → 422
        assert response.status_code == 422

    def test_create_schedule_missing_fields(self, client, auth_headers):
        response = client.post(
            "/api/v1/schedules",
            json={"name": "Bad"},
            headers=auth_headers,
        )
        assert response.status_code == 422


class TestToggleSchedule:
    def test_toggle_schedule(self, client, auth_headers, db_session, sample_device):
        plan = Plan(
            name="toggle-plan",
            description="for toggle test",
            failure_threshold=0.05,
                    )
        db_session.add(plan)
        db_session.commit()

        r = client.post(
            "/api/v1/schedules",
            json={
                "name": "Toggle",
                "cron_expr": "0 0 * * *",
                "plan_id": plan.id,
                "device_ids": [sample_device.id],
                "enabled": True,
            },
            headers=auth_headers,
        )
        assert r.status_code == 200
        sched_id = r.json()["id"]
        resp = client.post(f"/api/v1/schedules/{sched_id}/toggle", headers=auth_headers)
        assert resp.status_code == 200


class TestPlanSchedule:
    @staticmethod
    def _insert_legacy_plan(db_session) -> int:
        plan = Plan(
            name="legacy-sched-plan",
            description="legacy aee schedule plan",
            failure_threshold=0.05,
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

    @staticmethod
    def _insert_legacy_plan_schedule(db_session, plan_id: int, device_id: int) -> int:
        sched = TaskSchedule(
            name="Hidden Legacy Schedule",
            cron_expression="0 6 * * *",
            plan_id=plan_id,
            device_ids=[device_id],
            enabled=True,
        )
        db_session.add(sched)
        db_session.commit()
        db_session.refresh(sched)
        return int(sched.id)

    def test_create_plan_schedule(self, client, auth_headers, db_session, sample_device):
        plan = Plan(
            name="sched-plan",
            description="for schedule",
            failure_threshold=0.05,
                    )
        db_session.add(plan)
        db_session.commit()

        response = client.post(
            "/api/v1/schedules",
            json={
                "name": "Plan Daily",
                "cron_expr": "0 3 * * *",
                "plan_id": plan.id,
                "device_ids": [sample_device.id],
                "enabled": True,
            },
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["plan_id"] == plan.id
        assert data["device_ids"] == [sample_device.id]

    def test_create_schedule_rejects_hidden_legacy_plan(
        self, client, auth_headers, db_session, sample_device,
    ):
        legacy_plan_id = self._insert_legacy_plan(db_session)

        response = client.post(
            "/api/v1/schedules",
            json={
                "name": "Legacy Plan Daily",
                "cron_expr": "0 3 * * *",
                "plan_id": legacy_plan_id,
                "device_ids": [sample_device.id],
                "enabled": True,
            },
            headers=auth_headers,
        )

        assert response.status_code == 400, response.text

    def test_list_schedules_hides_existing_legacy_plan_schedule(
        self, client, auth_headers, db_session, sample_device,
    ):
        legacy_plan_id = self._insert_legacy_plan(db_session)
        schedule_id = self._insert_legacy_plan_schedule(
            db_session, legacy_plan_id, sample_device.id
        )

        resp = client.get("/api/v1/schedules", headers=auth_headers)

        assert resp.status_code == 200
        ids = {item["id"] for item in resp.json()["items"]}
        assert schedule_id not in ids

    def test_get_schedule_hides_existing_legacy_plan_schedule(
        self, client, auth_headers, db_session, sample_device,
    ):
        legacy_plan_id = self._insert_legacy_plan(db_session)
        schedule_id = self._insert_legacy_plan_schedule(
            db_session, legacy_plan_id, sample_device.id
        )

        resp = client.get(f"/api/v1/schedules/{schedule_id}", headers=auth_headers)

        assert resp.status_code == 404, resp.text

    def test_update_schedule_hides_existing_legacy_plan_schedule(
        self, client, auth_headers, db_session, sample_device,
    ):
        legacy_plan_id = self._insert_legacy_plan(db_session)
        schedule_id = self._insert_legacy_plan_schedule(
            db_session, legacy_plan_id, sample_device.id
        )

        resp = client.put(
            f"/api/v1/schedules/{schedule_id}",
            json={"name": "renamed"},
            headers=auth_headers,
        )

        assert resp.status_code == 404, resp.text

    def test_delete_schedule_hides_existing_legacy_plan_schedule(
        self, client, auth_headers, db_session, sample_device,
    ):
        legacy_plan_id = self._insert_legacy_plan(db_session)
        schedule_id = self._insert_legacy_plan_schedule(
            db_session, legacy_plan_id, sample_device.id
        )

        resp = client.delete(f"/api/v1/schedules/{schedule_id}", headers=auth_headers)

        assert resp.status_code == 404, resp.text

    def test_run_now_hides_existing_legacy_plan_schedule(
        self, client, auth_headers, db_session, sample_device,
    ):
        legacy_plan_id = self._insert_legacy_plan(db_session)
        schedule_id = self._insert_legacy_plan_schedule(
            db_session, legacy_plan_id, sample_device.id
        )

        resp = client.post(f"/api/v1/schedules/{schedule_id}/run-now", headers=auth_headers)

        assert resp.status_code == 404, resp.text

    def test_toggle_hides_existing_legacy_plan_schedule(
        self, client, auth_headers, db_session, sample_device,
    ):
        legacy_plan_id = self._insert_legacy_plan(db_session)
        schedule_id = self._insert_legacy_plan_schedule(
            db_session, legacy_plan_id, sample_device.id
        )

        resp = client.post(f"/api/v1/schedules/{schedule_id}/toggle", headers=auth_headers)

        assert resp.status_code == 404, resp.text

    def test_run_now_plan_schedule(self, client, auth_headers, db_session, sample_device, monkeypatch):
        plan = Plan(
            name="sched-run-now",
            description="run now plan",
            failure_threshold=0.05,
                    )
        db_session.add(plan)
        db_session.commit()

        create_resp = client.post(
            "/api/v1/schedules",
            json={
                "name": "Plan RunNow",
                "cron_expr": "0 4 * * *",
                "plan_id": plan.id,
                "device_ids": [sample_device.id],
                "enabled": True,
            },
            headers=auth_headers,
        )
        assert create_resp.status_code == 200
        sched_id = create_resp.json()["id"]

        monkeypatch.setattr(
            "backend.api.routes.schedules._dispatch_plan_sync_wrapper",
            lambda plan_id, device_ids: 9527,
        )

        run_resp = client.post(f"/api/v1/schedules/{sched_id}/run-now", headers=auth_headers)
        assert run_resp.status_code == 200
        payload = run_resp.json()
        assert payload["plan_run_id"] == 9527

    def test_run_now_returns_400_invalid_script_refs(
        self, client, auth_headers, db_session, sample_device, monkeypatch,
    ):
        """ADR-0023 C1:wrapper 抛 PlanDispatchError(missing_scripts=...)
        时,/schedules/{id}/run-now 必须与 plans 端点同形状(400 + INVALID_SCRIPT_REFS)。"""
        plan = Plan(
            name="sched-run-now-failfast",
            description="run now plan with missing script",
            failure_threshold=0.05,
        )
        db_session.add(plan)
        db_session.commit()

        create_resp = client.post(
            "/api/v1/schedules",
            json={
                "name": "Plan RunNow FailFast",
                "cron_expr": "0 5 * * *",
                "plan_id": plan.id,
                "device_ids": [sample_device.id],
                "enabled": True,
            },
            headers=auth_headers,
        )
        assert create_resp.status_code == 200
        sched_id = create_resp.json()["id"]

        from backend.services.plan_dispatcher_sync import PlanDispatchError

        def _wrapper_raises(plan_id, device_ids):
            raise PlanDispatchError(
                "scripts unavailable: check_device:1.0.0",
                missing_scripts=["check_device:1.0.0"],
            )

        monkeypatch.setattr(
            "backend.api.routes.schedules._dispatch_plan_sync_wrapper",
            _wrapper_raises,
        )

        run_resp = client.post(
            f"/api/v1/schedules/{sched_id}/run-now",
            headers=auth_headers,
        )
        assert run_resp.status_code == 400, run_resp.text
        detail = run_resp.json()["detail"]
        assert detail["code"] == "INVALID_SCRIPT_REFS"
        assert detail["missing"] == ["check_device:1.0.0"]
