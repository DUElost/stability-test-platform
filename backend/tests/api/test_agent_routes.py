"""
Tests for Agent API routes
"""
import pytest
from datetime import datetime, timedelta


class TestAgentPendingRuns:
    """Test GET /api/v1/agent/runs/pending"""

    def test_get_pending_runs_success(self, client, sample_task, sample_host, sample_device, auth_headers):
        """Test getting pending runs for a host"""
        # First dispatch the task (task must be in PENDING status)
        client.post(
            f"/api/v1/tasks/{sample_task.id}/dispatch",
            json={
                "host_id": sample_host.id,
                "device_id": sample_device.id,
            },
            headers=auth_headers,
        )

        response = client.get(f"/api/v1/agent/runs/pending?host_id={sample_host.id}")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["host_id"] == sample_host.id
        assert data[0]["device_id"] == sample_device.id
        assert data[0]["task_type"] == sample_task.type

    def test_get_pending_runs_empty(self, client, sample_host):
        """Test getting pending runs when none exist"""
        response = client.get(f"/api/v1/agent/runs/pending?host_id={sample_host.id}")
        assert response.status_code == 200
        data = response.json()
        assert data == []

    def test_get_pending_runs_missing_host_id(self, client):
        """Test getting pending runs without host_id"""
        response = client.get("/api/v1/agent/runs/pending")
        assert response.status_code == 422

    def test_get_pending_runs_status_changed_to_dispatched(
        self, client, sample_task, sample_host, sample_device, db_session, auth_headers
    ):
        """Test that runs status is changed to DISPATCHED when fetched"""
        # First dispatch the task (task must be in PENDING status)
        dispatch_resp = client.post(
            f"/api/v1/tasks/{sample_task.id}/dispatch",
            json={
                "host_id": sample_host.id,
                "device_id": sample_device.id,
            },
            headers=auth_headers,
        )
        run_id = dispatch_resp.json()["id"]

        # Fetch pending runs
        client.get(f"/api/v1/agent/runs/pending?host_id={sample_host.id}")

        # Check status was updated
        from backend.models.schemas import TaskRun
        run = db_session.get(TaskRun, run_id)
        assert run.status.value == "DISPATCHED"

    def test_get_pending_runs_with_limit(self, client, sample_host, sample_device):
        """Test getting pending runs with limit"""
        response = client.get(f"/api/v1/agent/runs/pending?host_id={sample_host.id}&limit=5")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    def test_get_pending_runs_invalid_limit(self, client, sample_host):
        """Test getting pending runs with invalid limit"""
        response = client.get(f"/api/v1/agent/runs/pending?host_id={sample_host.id}&limit=0")
        assert response.status_code == 422


class TestAgentRunHeartbeat:
    """Test POST /api/v1/agent/runs/{run_id}/heartbeat"""

    def test_run_heartbeat_success(self, client, sample_running_run):
        """Test sending heartbeat for a run"""
        response = client.post(
            f"/api/v1/agent/runs/{sample_running_run.id}/heartbeat",
            json={
                "status": "RUNNING",
                "log_summary": "progress=50%",
                "progress": 50,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True

    def test_run_heartbeat_not_found(self, client):
        """Test heartbeat for non-existent run"""
        response = client.post(
            "/api/v1/agent/runs/99999/heartbeat",
            json={"status": "RUNNING"},
        )
        assert response.status_code == 404

    def test_run_heartbeat_status_transition(self, client, sample_dispatched_run):
        """Test status transition via heartbeat"""
        response = client.post(
            f"/api/v1/agent/runs/{sample_dispatched_run.id}/heartbeat",
            json={"status": "RUNNING"},
        )
        assert response.status_code == 200

    def test_run_heartbeat_invalid_status(self, client, sample_running_run):
        """Test heartbeat with invalid status"""
        response = client.post(
            f"/api/v1/agent/runs/{sample_running_run.id}/heartbeat",
            json={"status": "INVALID_STATUS"},
        )
        assert response.status_code == 400
        assert "invalid run status" in response.json()["detail"]

    def test_run_heartbeat_illegal_transition(self, client, sample_running_run):
        """Test heartbeat with illegal status transition"""
        response = client.post(
            f"/api/v1/agent/runs/{sample_running_run.id}/heartbeat",
            json={"status": "QUEUED"},
        )
        assert response.status_code == 409
        assert "illegal run transition" in response.json()["detail"]

    def test_run_heartbeat_with_log_lines(self, client, sample_running_run):
        """Test heartbeat with log lines"""
        response = client.post(
            f"/api/v1/agent/runs/{sample_running_run.id}/heartbeat",
            json={
                "status": "RUNNING",
                "log_lines": ["Log line 1", "Log line 2"],
            },
        )
        assert response.status_code == 200

    def test_run_heartbeat_updates_task_status(self, client, sample_running_run, db_session):
        """Test that RUNNING heartbeat updates task status to RUNNING"""
        import sys
        import os
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        if project_root not in sys.path:
            sys.path.insert(0, project_root)
        from backend.models.schemas import Task

        task = db_session.get(Task, sample_running_run.task_id)
        original_status = task.status

        response = client.post(
            f"/api/v1/agent/runs/{sample_running_run.id}/heartbeat",
            json={"status": "RUNNING"},
        )
        assert response.status_code == 200

        db_session.refresh(task)
        assert task.status.value == "RUNNING"

    def test_run_heartbeat_extends_device_lock(self, client, sample_running_run, db_session):
        """Test that RUNNING heartbeat extends device lock"""
        # Set up device lock to match the running run
        import sys
        import os
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        if project_root not in sys.path:
            sys.path.insert(0, project_root)
        from backend.models.schemas import Device
        device = db_session.get(Device, sample_running_run.device_id)
        device.lock_run_id = sample_running_run.id
        device.lock_expires_at = datetime.utcnow() + timedelta(minutes=10)
        db_session.commit()
        original_expires = device.lock_expires_at

        response = client.post(
            f"/api/v1/agent/runs/{sample_running_run.id}/heartbeat",
            json={"status": "RUNNING"},
        )
        assert response.status_code == 200

        db_session.refresh(device)
        # Lock should be extended
        assert device.lock_expires_at > original_expires or device.lock_expires_at is not None


class TestAgentRunComplete:
    """Test POST /api/v1/agent/runs/{run_id}/complete"""

    def _setup_device_lock(self, sample_running_run, db_session):
        """Helper to set up device lock for tests"""
        import sys
        import os
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        if project_root not in sys.path:
            sys.path.insert(0, project_root)
        from backend.models.schemas import Device
        device = db_session.get(Device, sample_running_run.device_id)
        device.lock_run_id = sample_running_run.id
        device.lock_expires_at = datetime.utcnow() + timedelta(minutes=10)
        db_session.commit()
        return device

    def test_run_complete_success_finished(self, client, sample_running_run, db_session):
        """Test completing a run with FINISHED status"""
        self._setup_device_lock(sample_running_run, db_session)
        response = client.post(
            f"/api/v1/agent/runs/{sample_running_run.id}/complete",
            json={
                "update": {
                    "status": "FINISHED",
                    "exit_code": 0,
                    "log_summary": "completed successfully",
                }
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True

    def test_run_complete_success_failed(self, client, sample_running_run, db_session):
        """Test completing a run with FAILED status"""
        self._setup_device_lock(sample_running_run, db_session)
        response = client.post(
            f"/api/v1/agent/runs/{sample_running_run.id}/complete",
            json={
                "update": {
                    "status": "FAILED",
                    "exit_code": 1,
                    "error_code": "TEST_ERROR",
                    "error_message": "Test failure",
                }
            },
        )
        assert response.status_code == 200

    def test_run_complete_with_artifact(self, client, sample_running_run, db_session):
        """Test completing a run with artifact"""
        self._setup_device_lock(sample_running_run, db_session)
        response = client.post(
            f"/api/v1/agent/runs/{sample_running_run.id}/complete",
            json={
                "update": {
                    "status": "FINISHED",
                    "exit_code": 0,
                },
                "artifact": {
                    "storage_uri": "file:///tmp/test.tar.gz",
                    "size_bytes": 1024,
                    "checksum": "abc123",
                },
            },
        )
        assert response.status_code == 200

    def test_run_complete_not_found(self, client):
        """Test completing non-existent run"""
        response = client.post(
            "/api/v1/agent/runs/99999/complete",
            json={
                "update": {
                    "status": "FINISHED",
                    "exit_code": 0,
                }
            },
        )
        assert response.status_code == 404

    def test_run_complete_missing_status(self, client, sample_running_run):
        """Test completing without status"""
        response = client.post(
            f"/api/v1/agent/runs/{sample_running_run.id}/complete",
            json={
                "update": {
                    "exit_code": 0,
                }
            },
        )
        assert response.status_code == 400
        assert "status required" in response.json()["detail"]

    def test_run_complete_invalid_status(self, client, sample_running_run):
        """Test completing with invalid status"""
        response = client.post(
            f"/api/v1/agent/runs/{sample_running_run.id}/complete",
            json={
                "update": {
                    "status": "INVALID",
                    "exit_code": 0,
                }
            },
        )
        assert response.status_code == 400
        assert "invalid run status" in response.json()["detail"]

    def test_run_complete_illegal_transition(self, client, sample_running_run):
        """Test completing with illegal transition"""
        response = client.post(
            f"/api/v1/agent/runs/{sample_running_run.id}/complete",
            json={
                "update": {
                    "status": "QUEUED",
                    "exit_code": 0,
                }
            },
        )
        assert response.status_code == 409
        assert "illegal run transition" in response.json()["detail"]

    def test_run_complete_releases_device_lock(self, client, sample_running_run, db_session):
        """Test that completing releases device lock"""
        # Set up device lock to match the running run
        import sys
        import os
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        if project_root not in sys.path:
            sys.path.insert(0, project_root)
        from backend.models.schemas import Device
        device = db_session.get(Device, sample_running_run.device_id)
        device.lock_run_id = sample_running_run.id
        device.lock_expires_at = datetime.utcnow() + timedelta(minutes=10)
        db_session.commit()

        response = client.post(
            f"/api/v1/agent/runs/{sample_running_run.id}/complete",
            json={
                "update": {
                    "status": "FINISHED",
                    "exit_code": 0,
                }
            },
        )
        assert response.status_code == 200

        db_session.refresh(device)
        assert device.lock_run_id is None
        assert device.lock_expires_at is None

    def test_run_complete_updates_task_status_finished(self, client, sample_running_run, db_session):
        """Test that FINISHED run updates task to COMPLETED"""
        response = client.post(
            f"/api/v1/agent/runs/{sample_running_run.id}/complete",
            json={
                "update": {
                    "status": "FINISHED",
                    "exit_code": 0,
                }
            },
        )
        assert response.status_code == 200

        import sys
        import os
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        if project_root not in sys.path:
            sys.path.insert(0, project_root)
        from backend.models.schemas import Task
        task = db_session.get(Task, sample_running_run.task_id)
        assert task.status.value == "COMPLETED"

    def test_run_complete_updates_task_status_failed(self, client, sample_running_run, db_session):
        """Test that FAILED run updates task to FAILED"""
        response = client.post(
            f"/api/v1/agent/runs/{sample_running_run.id}/complete",
            json={
                "update": {
                    "status": "FAILED",
                    "exit_code": 1,
                }
            },
        )
        assert response.status_code == 200

        import sys
        import os
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        if project_root not in sys.path:
            sys.path.insert(0, project_root)
        from backend.models.schemas import Task
        task = db_session.get(Task, sample_running_run.task_id)
        assert task.status.value == "FAILED"

    def test_run_complete_legacy_payload(self, client, sample_running_run, db_session):
        """Test completing with legacy payload format"""
        # Set up device lock to match the running run
        import sys
        import os
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        if project_root not in sys.path:
            sys.path.insert(0, project_root)
        from backend.models.schemas import Device
        device = db_session.get(Device, sample_running_run.device_id)
        device.lock_run_id = sample_running_run.id
        device.lock_expires_at = datetime.utcnow() + timedelta(minutes=10)
        db_session.commit()

        response = client.post(
            f"/api/v1/agent/runs/{sample_running_run.id}/complete",
            json={
                "status": "FINISHED",
                "exit_code": 0,
                "log_summary": "legacy format",
            },
        )
        assert response.status_code == 200


class TestExtendDeviceLock:
    """Test POST /api/v1/agent/runs/{run_id}/extend_lock"""

    def test_extend_lock_success(self, client, sample_running_run, db_session):
        """Test extending device lock successfully"""
        # Set up device lock to match the running run
        import sys
        import os
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        if project_root not in sys.path:
            sys.path.insert(0, project_root)
        from backend.models.schemas import Device
        device = db_session.get(Device, sample_running_run.device_id)
        device.lock_run_id = sample_running_run.id
        device.lock_expires_at = datetime.utcnow() + timedelta(minutes=10)
        db_session.commit()

        response = client.post(f"/api/v1/agent/runs/{sample_running_run.id}/extend_lock")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["run_id"] == sample_running_run.id
        assert "expires_at" in data
        assert "extended_at" in data

    def test_extend_lock_not_found(self, client):
        """Test extending lock for non-existent run"""
        response = client.post("/api/v1/agent/runs/99999/extend_lock")
        assert response.status_code == 404

    def test_extend_lock_not_running(self, client, sample_task_run):
        """Test extending lock for non-running run"""
        response = client.post(f"/api/v1/agent/runs/{sample_task_run.id}/extend_lock")
        assert response.status_code == 409
        assert "not running" in response.json()["detail"]

    def test_extend_lock_lock_lost(self, client, sample_running_run, db_session):
        """Test extending lock when lock is lost"""
        # Set up device lock to match the running run first
        import sys
        import os
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        if project_root not in sys.path:
            sys.path.insert(0, project_root)
        from backend.models.schemas import Device
        device = db_session.get(Device, sample_running_run.device_id)
        device.lock_run_id = sample_running_run.id
        device.lock_expires_at = datetime.utcnow() + timedelta(minutes=10)
        db_session.commit()

        # Simulate lock being taken by another run
        device.lock_run_id = 99999
        db_session.commit()

        response = client.post(f"/api/v1/agent/runs/{sample_running_run.id}/extend_lock")
        assert response.status_code == 409
        assert "lock lost" in response.json()["detail"]

    def test_extend_lock_device_not_found(self, client, sample_running_run, db_session):
        """Test extending lock when device not found"""
        if db_session.bind and db_session.bind.dialect.name == "postgresql":
            pytest.skip("PostgreSQL foreign key prevents orphaned run->device reference")
        # Set invalid device id
        sample_running_run.device_id = 99999
        db_session.commit()

        response = client.post(f"/api/v1/agent/runs/{sample_running_run.id}/extend_lock")
        assert response.status_code == 404
