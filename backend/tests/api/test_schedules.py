"""Tests for schedules API routes — ADR-0020 (Plan-based)."""
import pytest
from datetime import datetime, timezone

from backend.models.plan import Plan


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
