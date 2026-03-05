"""Tests for workflow API routes (orchestration v2)."""

import os
import pytest


pytestmark = pytest.mark.skipif(
    os.name == "nt",
    reason="Windows + asyncpg + TestClient 在本地环境下存在不稳定事件循环问题，工作流接口在 Linux CI 验证",
)


class TestListWorkflows:
    def test_list_workflows(self, client):
        response = client.get("/api/v1/workflows")
        assert response.status_code == 200
        data = response.json()
        assert "data" in data
        assert "error" in data
        assert isinstance(data["data"], list)


class TestCreateWorkflow:
    def test_create_workflow(self, client):
        response = client.post(
            "/api/v1/workflows",
            json={
                "name": "Test Workflow",
                "description": "A test workflow",
                "failure_threshold": 0.05,
                "task_templates": [
                    {
                        "name": "Template 1",
                        "pipeline_def": {
                            "version": 1,
                            "phases": [
                                {
                                    "name": "prepare",
                                    "parallel": False,
                                    "steps": [
                                        {
                                            "name": "check_device",
                                            "action": "builtin:check_device",
                                            "params": {},
                                        }
                                    ],
                                }
                            ],
                        },
                    }
                ],
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["error"] is None
        assert body["data"]["name"] == "Test Workflow"


class TestWorkflowOperations:
    def test_get_workflow_not_found(self, client):
        resp = client.get("/api/v1/workflows/99999")
        assert resp.status_code == 404
