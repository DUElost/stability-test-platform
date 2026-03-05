"""Tests for schedules API routes"""
import pytest
from datetime import datetime

from backend.models.workflow import WorkflowDefinition


class TestListSchedules:
    def test_list_schedules_empty(self, client, auth_headers):
        response = client.get("/api/v1/schedules", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0


class TestCreateSchedule:
    def test_create_schedule(self, client, auth_headers):
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
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Daily Monkey"
        assert data["cron_expr"] == "0 8 * * *"

    def test_create_schedule_missing_fields(self, client, auth_headers):
        response = client.post(
            "/api/v1/schedules",
            json={"name": "Bad"},
            headers=auth_headers,
        )
        assert response.status_code == 422


class TestToggleSchedule:
    def test_toggle_schedule(self, client, auth_headers):
        r = client.post(
            "/api/v1/schedules",
            json={
                "name": "Toggle",
                "cron_expr": "0 0 * * *",
                "task_name": "T",
                "task_type": "MONKEY",
                "task_params": {},
                "enabled": True,
            },
            headers=auth_headers,
        )
        sched_id = r.json()["id"]
        resp = client.post(f"/api/v1/schedules/{sched_id}/toggle", headers=auth_headers)
        assert resp.status_code == 200


class TestWorkflowSchedule:
    def test_create_workflow_schedule(self, client, auth_headers, db_session, sample_device):
        now = datetime.utcnow()
        wf = WorkflowDefinition(
            name="sched-workflow",
            description="for schedule",
            failure_threshold=0.05,
            created_at=now,
            updated_at=now,
        )
        db_session.add(wf)
        db_session.commit()

        response = client.post(
            "/api/v1/schedules",
            json={
                "name": "Workflow Daily",
                "cron_expr": "0 3 * * *",
                "workflow_definition_id": wf.id,
                "device_ids": [sample_device.id],
                "enabled": True,
            },
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["workflow_definition_id"] == wf.id
        assert data["device_ids"] == [sample_device.id]
        assert data["task_type"] == "WORKFLOW"

    def test_run_now_workflow_schedule(self, client, auth_headers, db_session, sample_device, monkeypatch):
        now = datetime.utcnow()
        wf = WorkflowDefinition(
            name="sched-run-now",
            description="run now workflow",
            failure_threshold=0.05,
            created_at=now,
            updated_at=now,
        )
        db_session.add(wf)
        db_session.commit()

        create_resp = client.post(
            "/api/v1/schedules",
            json={
                "name": "Workflow RunNow",
                "cron_expr": "0 4 * * *",
                "workflow_definition_id": wf.id,
                "device_ids": [sample_device.id],
                "enabled": True,
            },
            headers=auth_headers,
        )
        assert create_resp.status_code == 200
        sched_id = create_resp.json()["id"]

        monkeypatch.setattr(
            "backend.api.routes.schedules._dispatch_workflow_sync",
            lambda workflow_definition_id, device_ids: 9527,
        )

        run_resp = client.post(f"/api/v1/schedules/{sched_id}/run-now", headers=auth_headers)
        assert run_resp.status_code == 200
        payload = run_resp.json()
        assert payload["workflow_run_id"] == 9527
        assert payload["task_id"] is None
