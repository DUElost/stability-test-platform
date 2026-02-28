"""
Tests for tasks API routes
"""
import pytest
from datetime import datetime, timedelta

def make_pipeline_def():
    return {
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
                        "timeout": 30,
                        "on_failure": "stop",
                        "max_retries": 0,
                    }
                ],
            }
        ],
    }


class TestListTasks:
    """Test GET /api/v1/tasks"""

    def test_list_tasks_empty(self, client):
        """Test listing tasks when empty"""
        response = client.get("/api/v1/tasks")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 0

    def test_list_tasks_with_data(self, client, sample_task):
        """Test listing tasks with data"""
        response = client.get("/api/v1/tasks")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["name"] == sample_task.name
        assert data[0]["type"] == sample_task.type

    def test_list_tasks_ordered_by_id_desc(self, client, sample_host, sample_device, auth_headers):
        """Test tasks are ordered by id descending"""
        # Create multiple tasks
        for i in range(3):
            client.post(
                "/api/v1/tasks",
                json={
                    "name": f"order-task-{i}",
                    "type": "MONKEY",
                    "target_device_id": sample_device.id,
                    "pipeline_def": make_pipeline_def(),
                },
                headers=auth_headers,
            )

        response = client.get("/api/v1/tasks")
        data = response.json()
        assert len(data) == 3
        ids = [d["id"] for d in data]
        assert ids == sorted(ids, reverse=True)


class TestGetTask:
    """Test GET /api/v1/tasks/{task_id}"""

    def test_get_task_success(self, client, sample_task):
        """Test getting a task by id"""
        response = client.get(f"/api/v1/tasks/{sample_task.id}")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == sample_task.id
        assert data["name"] == sample_task.name
        assert data["type"] == sample_task.type
        assert data["status"] == "PENDING"

    def test_get_task_not_found(self, client):
        """Test getting non-existent task"""
        response = client.get("/api/v1/tasks/99999")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"]

    def test_get_task_invalid_id(self, client):
        """Test getting task with invalid id"""
        response = client.get("/api/v1/tasks/invalid")
        assert response.status_code == 422


class TestCreateTask:
    """Test POST /api/v1/tasks"""

    def test_create_task_success(self, client, sample_device, auth_headers):
        """Test creating a new task successfully"""
        response = client.post(
            "/api/v1/tasks",
            json={
                "name": "new-task",
                "type": "MONKEY",
                "params": {"count": 5000},
                "target_device_id": sample_device.id,
                "priority": 5,
                "pipeline_def": make_pipeline_def(),
            },
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "new-task"
        assert data["type"] == "MONKEY"
        assert data["params"] == {"count": 5000}
        assert data["target_device_id"] == sample_device.id
        assert data["priority"] == 5
        assert data["status"] == "PENDING"
        assert "id" in data

    def test_create_task_by_serial(self, client, sample_device, auth_headers):
        """Test creating task using device serial"""
        response = client.post(
            "/api/v1/tasks",
            json={
                "name": "serial-task",
                "type": "MTBF",
                "device_serial": sample_device.serial,
                "pipeline_def": make_pipeline_def(),
            },
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["target_device_id"] == sample_device.id

    def test_create_task_invalid_serial(self, client, auth_headers):
        """Test creating task with invalid device serial"""
        response = client.post(
            "/api/v1/tasks",
            json={
                "name": "invalid-serial-task",
                "type": "MONKEY",
                "device_serial": "NONEXISTENT",
            },
            headers=auth_headers,
        )
        assert response.status_code == 400
        assert "serial not found" in response.json()["detail"]

    def test_create_task_device_not_online(self, client, sample_offline_device, auth_headers):
        """Test creating task when device is not online"""
        response = client.post(
            "/api/v1/tasks",
            json={
                "name": "offline-device-task",
                "type": "MONKEY",
                "target_device_id": sample_offline_device.id,
            },
            headers=auth_headers,
        )
        assert response.status_code == 409
        assert "not online" in response.json()["detail"]

    def test_create_task_device_no_host(self, client, db_session, auth_headers):
        """Test creating task when device has no host"""
        import sys
        import os
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        if project_root not in sys.path:
            sys.path.insert(0, project_root)
        from backend.models.schemas import Device, DeviceStatus

        device = Device(
            serial="NOHOST001",
            status=DeviceStatus.ONLINE,
            last_seen=datetime.utcnow(),
        )
        db_session.add(device)
        db_session.commit()

        response = client.post(
            "/api/v1/tasks",
            json={
                "name": "no-host-task",
                "type": "MONKEY",
                "target_device_id": device.id,
            },
            headers=auth_headers,
        )
        assert response.status_code == 409
        assert "no host binding" in response.json()["detail"]

    def test_create_task_host_not_online(self, client, db_session, sample_offline_host, auth_headers):
        """Test creating task when host is not online"""
        import sys
        import os
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        if project_root not in sys.path:
            sys.path.insert(0, project_root)
        from backend.models.schemas import Device, DeviceStatus

        device = Device(
            serial="OFFLINEHOST001",
            host_id=sample_offline_host.id,
            status=DeviceStatus.ONLINE,
            last_seen=datetime.utcnow(),
        )
        db_session.add(device)
        db_session.commit()

        response = client.post(
            "/api/v1/tasks",
            json={
                "name": "offline-host-task",
                "type": "MONKEY",
                "target_device_id": device.id,
            },
            headers=auth_headers,
        )
        assert response.status_code == 409
        assert "host is not online" in response.json()["detail"]

    def test_create_task_missing_name(self, client, sample_device, auth_headers):
        """Test creating task without name fails"""
        response = client.post(
            "/api/v1/tasks",
            json={
                "type": "MONKEY",
                "target_device_id": sample_device.id,
            },
            headers=auth_headers,
        )
        assert response.status_code == 422

    def test_create_task_missing_type(self, client, sample_device, auth_headers):
        """Test creating task without type fails"""
        response = client.post(
            "/api/v1/tasks",
            json={
                "name": "no-type-task",
                "target_device_id": sample_device.id,
            },
            headers=auth_headers,
        )
        assert response.status_code == 422

    def test_create_task_default_priority(self, client, sample_device, auth_headers):
        """Test creating task with default priority"""
        response = client.post(
            "/api/v1/tasks",
            json={
                "name": "default-priority-task",
                "type": "MONKEY",
                "target_device_id": sample_device.id,
                "pipeline_def": make_pipeline_def(),
            },
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["priority"] == 0


class TestGetTaskRuns:
    """Test GET /api/v1/tasks/{task_id}/runs"""

    def test_get_task_runs_success(self, client, sample_task, sample_task_run):
        """Test getting runs for a task"""
        response = client.get(f"/api/v1/tasks/{sample_task.id}/runs")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["task_id"] == sample_task.id

    def test_get_task_runs_task_not_found(self, client):
        """Test getting runs for non-existent task"""
        response = client.get("/api/v1/tasks/99999/runs")
        assert response.status_code == 404

    def test_get_task_runs_empty(self, client, sample_task):
        """Test getting runs when task has no runs"""
        response = client.get(f"/api/v1/tasks/{sample_task.id}/runs")
        assert response.status_code == 200
        data = response.json()
        assert data == []


class TestDispatchTask:
    """Test POST /api/v1/tasks/{task_id}/dispatch"""

    def test_dispatch_task_success(self, client, sample_task, sample_host, sample_device, auth_headers):
        """Test dispatching a task successfully"""
        response = client.post(
            f"/api/v1/tasks/{sample_task.id}/dispatch",
            json={
                "host_id": sample_host.id,
                "device_id": sample_device.id,
            },
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["task_id"] == sample_task.id
        assert data["host_id"] == sample_host.id
        assert data["device_id"] == sample_device.id
        assert data["status"] == "QUEUED"

    def test_dispatch_task_not_found(self, client, sample_host, sample_device, auth_headers):
        """Test dispatching non-existent task"""
        response = client.post(
            "/api/v1/tasks/99999/dispatch",
            json={
                "host_id": sample_host.id,
                "device_id": sample_device.id,
            },
            headers=auth_headers,
        )
        assert response.status_code == 404

    def test_dispatch_task_host_not_found(self, client, sample_task, sample_device, auth_headers):
        """Test dispatching with non-existent host"""
        response = client.post(
            f"/api/v1/tasks/{sample_task.id}/dispatch",
            json={
                "host_id": 99999,
                "device_id": sample_device.id,
            },
            headers=auth_headers,
        )
        assert response.status_code == 400

    def test_dispatch_task_device_not_found(self, client, sample_task, sample_host, auth_headers):
        """Test dispatching with non-existent device"""
        response = client.post(
            f"/api/v1/tasks/{sample_task.id}/dispatch",
            json={
                "host_id": sample_host.id,
                "device_id": 99999,
            },
            headers=auth_headers,
        )
        assert response.status_code == 400

    def test_dispatch_task_target_mismatch(self, client, sample_task, sample_host, sample_device, db_session, auth_headers):
        """Test dispatching with mismatched target device"""
        # Set a different target device
        import sys
        import os
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        if project_root not in sys.path:
            sys.path.insert(0, project_root)
        from backend.models.schemas import Device, DeviceStatus
        other_device = Device(
            serial="OTHER001",
            host_id=sample_host.id,
            status=DeviceStatus.ONLINE,
            last_seen=datetime.utcnow(),
        )
        db_session.add(other_device)
        db_session.commit()

        sample_task.target_device_id = other_device.id
        db_session.commit()

        response = client.post(
            f"/api/v1/tasks/{sample_task.id}/dispatch",
            json={
                "host_id": sample_host.id,
                "device_id": sample_device.id,
            },
            headers=auth_headers,
        )
        assert response.status_code == 409
        assert "target device mismatch" in response.json()["detail"]

    def test_dispatch_task_not_pending(self, client, sample_queued_task, sample_host, sample_device, auth_headers):
        """Test dispatching task that is not pending"""
        response = client.post(
            f"/api/v1/tasks/{sample_queued_task.id}/dispatch",
            json={
                "host_id": sample_host.id,
                "device_id": sample_device.id,
            },
            headers=auth_headers,
        )
        assert response.status_code == 409
        assert "task not pending" in response.json()["detail"]


class TestTaskTemplates:
    """Test GET /api/v1/task-templates"""

    def test_list_task_templates(self, client):
        """Test listing task templates"""
        response = client.get("/api/v1/task-templates")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        # Should have at least some templates
        assert len(data) > 0

    def test_task_template_structure(self, client):
        """Test task template has correct structure"""
        response = client.get("/api/v1/task-templates")
        assert response.status_code == 200
        data = response.json()
        if len(data) > 0:
            template = data[0]
            assert "type" in template
            assert "name" in template
            assert "description" in template
            assert "default_params" in template
            assert "script_paths" in template
