"""Tests for schedules API routes"""
import pytest


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
