"""
Contract tests for the intentional removal of the legacy /tasks* compatibility layer.
"""


class TestLegacyTasksEndpointsRemoved:
    def test_list_tasks_endpoint_removed(self, client):
        response = client.get("/api/v1/tasks")
        assert response.status_code == 404

    def test_create_task_endpoint_removed(self, client, auth_headers):
        response = client.post(
            "/api/v1/tasks",
            json={"name": "legacy-task", "type": "MONKEY"},
            headers=auth_headers,
        )
        assert response.status_code == 404

    def test_task_runs_endpoint_removed(self, client):
        response = client.get("/api/v1/tasks/1/runs")
        assert response.status_code == 404

    def test_dispatch_task_endpoint_removed(self, client, auth_headers):
        response = client.post(
            "/api/v1/tasks/1/dispatch",
            json={"host_id": "101", "device_id": 1},
            headers=auth_headers,
        )
        assert response.status_code == 404

    def test_task_templates_endpoint_removed(self, client):
        response = client.get("/api/v1/task-templates")
        assert response.status_code == 404
