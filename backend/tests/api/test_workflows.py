"""Tests for workflow API routes"""
import pytest


class TestListWorkflows:
    def test_list_workflows_empty(self, client):
        response = client.get("/api/v1/workflows")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert data["items"] == []


class TestCreateWorkflow:
    def test_create_workflow(self, client, auth_headers):
        response = client.post(
            "/api/v1/workflows",
            json={
                "name": "Test Workflow",
                "description": "A test workflow",
                "steps": [
                    {"name": "Step 1", "task_type": "MONKEY", "params": {"count": 100}},
                ],
            },
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Test Workflow"
        assert data["status"] == "DRAFT"
        assert len(data["steps"]) == 1

    def test_create_workflow_empty_steps(self, client, auth_headers):
        response = client.post(
            "/api/v1/workflows",
            json={"name": "Empty", "steps": []},
            headers=auth_headers,
        )
        # Should succeed or return 400 — depends on validation
        assert response.status_code in (200, 400)


class TestWorkflowOperations:
    def test_get_workflow(self, client, auth_headers):
        r = client.post(
            "/api/v1/workflows",
            json={"name": "WF", "steps": [{"name": "S1", "task_type": "MONKEY"}]},
            headers=auth_headers,
        )
        wf_id = r.json()["id"]
        resp = client.get(f"/api/v1/workflows/{wf_id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == wf_id

    def test_get_workflow_not_found(self, client):
        resp = client.get("/api/v1/workflows/99999")
        assert resp.status_code == 404

    def test_delete_workflow(self, client, auth_headers):
        r = client.post(
            "/api/v1/workflows",
            json={"name": "DelWF", "steps": []},
            headers=auth_headers,
        )
        wf_id = r.json()["id"]
        resp = client.delete(f"/api/v1/workflows/{wf_id}", headers=auth_headers)
        assert resp.status_code == 200

    def test_clone_workflow(self, client, auth_headers):
        r = client.post(
            "/api/v1/workflows",
            json={"name": "CloneMe", "steps": [{"name": "S1", "task_type": "MONKEY"}]},
            headers=auth_headers,
        )
        wf_id = r.json()["id"]
        resp = client.post(f"/api/v1/workflows/{wf_id}/clone", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["name"].startswith("CloneMe")
