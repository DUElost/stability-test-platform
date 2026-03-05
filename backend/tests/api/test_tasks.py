"""
Tests for tasks API routes (new STP orchestration compatibility)
"""

import json
from datetime import datetime

from backend.models.job import JobInstance, StepTrace, TaskTemplate
from backend.models.workflow import WorkflowDefinition, WorkflowRun


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


def _seed_workflow(db_session, name: str) -> WorkflowDefinition:
    now = datetime.utcnow()
    wf = WorkflowDefinition(
        name=name,
        description="test workflow",
        failure_threshold=0.05,
        created_at=now,
        updated_at=now,
    )
    db_session.add(wf)
    db_session.commit()
    db_session.refresh(wf)
    return wf


class TestListTasks:
    """Test GET /api/v1/tasks"""

    def test_list_tasks_empty(self, client):
        """Use impossible status filter to assert empty result deterministically"""
        response = client.get("/api/v1/tasks", params={"status": "__NONE__"})
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert data == []

    def test_list_tasks_with_data(self, client, db_session):
        wf = _seed_workflow(db_session, name="task-view-workflow")

        response = client.get("/api/v1/tasks")
        assert response.status_code == 200
        data = response.json()

        item = next((x for x in data if x["id"] == wf.id), None)
        assert item is not None
        assert item["name"] == wf.name
        assert item["type"] == "WORKFLOW"

    def test_list_tasks_ordered_by_id_desc(self, client, db_session):
        wf1 = _seed_workflow(db_session, name="task-order-1")
        wf2 = _seed_workflow(db_session, name="task-order-2")

        response = client.get("/api/v1/tasks")
        assert response.status_code == 200
        data = response.json()

        ids = [x["id"] for x in data]
        assert ids == sorted(ids, reverse=True)
        assert ids.index(wf2.id) < ids.index(wf1.id)


class TestGetTask:
    """Test GET /api/v1/tasks/{task_id}"""

    def test_get_task_success(self, client, db_session):
        wf = _seed_workflow(db_session, name="task-get")

        response = client.get(f"/api/v1/tasks/{wf.id}")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == wf.id
        assert data["name"] == wf.name
        assert data["type"] == "WORKFLOW"

    def test_get_task_not_found(self, client):
        response = client.get("/api/v1/tasks/99999")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"]

    def test_get_task_invalid_id(self, client):
        response = client.get("/api/v1/tasks/invalid")
        assert response.status_code == 422


class TestCreateTask:
    """Test POST /api/v1/tasks (legacy endpoint now migrated)"""

    def test_create_task_returns_503(self, client, auth_headers):
        response = client.post(
            "/api/v1/tasks",
            json={
                "name": "new-task",
                "type": "MONKEY",
                "pipeline_def": make_pipeline_def(),
            },
            headers=auth_headers,
        )
        assert response.status_code == 503
        assert "迁移" in response.json()["detail"]

    def test_create_task_missing_name(self, client, auth_headers):
        response = client.post(
            "/api/v1/tasks",
            json={
                "type": "MONKEY",
            },
            headers=auth_headers,
        )
        assert response.status_code == 422


class TestGetTaskRuns:
    """Test GET /api/v1/tasks/{task_id}/runs"""

    def test_get_task_runs_success(self, client, db_session, sample_device):
        wf = _seed_workflow(db_session, name="runs-success")

        template = TaskTemplate(
            workflow_definition_id=wf.id,
            name="template-1",
            pipeline_def=make_pipeline_def(),
            sort_order=0,
            created_at=datetime.utcnow(),
        )
        db_session.add(template)
        db_session.flush()

        wf_run = WorkflowRun(
            workflow_definition_id=wf.id,
            status="RUNNING",
            failure_threshold=0.05,
            triggered_by="pytest",
            started_at=datetime.utcnow(),
        )
        db_session.add(wf_run)
        db_session.flush()

        job = JobInstance(
            workflow_run_id=wf_run.id,
            task_template_id=template.id,
            device_id=sample_device.id,
            host_id=sample_device.host_id,
            status="PENDING",
            pipeline_def=make_pipeline_def(),
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        db_session.add(job)
        db_session.commit()

        response = client.get(f"/api/v1/tasks/{wf.id}/runs")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 1
        assert data[0]["task_id"] == wf.id

    def test_get_task_runs_task_not_found(self, client):
        response = client.get("/api/v1/tasks/99999/runs")
        assert response.status_code == 404

    def test_get_task_runs_empty(self, client, db_session):
        wf = _seed_workflow(db_session, name="runs-empty")

        response = client.get(f"/api/v1/tasks/{wf.id}/runs")
        assert response.status_code == 200
        data = response.json()
        assert data == []

    def test_get_task_runs_all_with_task_id_zero(self, client, db_session, sample_device):
        wf1 = _seed_workflow(db_session, name="runs-all-1")
        wf2 = _seed_workflow(db_session, name="runs-all-2")

        t1 = TaskTemplate(
            workflow_definition_id=wf1.id,
            name="template-a",
            pipeline_def={"stages": {"prepare": [], "execute": [], "post_process": []}},
            sort_order=0,
            created_at=datetime.utcnow(),
        )
        t2 = TaskTemplate(
            workflow_definition_id=wf2.id,
            name="template-b",
            pipeline_def={"stages": {"prepare": [], "execute": [], "post_process": []}},
            sort_order=0,
            created_at=datetime.utcnow(),
        )
        db_session.add_all([t1, t2])
        db_session.flush()

        r1 = WorkflowRun(
            workflow_definition_id=wf1.id,
            status="RUNNING",
            failure_threshold=0.05,
            triggered_by="pytest",
            started_at=datetime.utcnow(),
        )
        r2 = WorkflowRun(
            workflow_definition_id=wf2.id,
            status="RUNNING",
            failure_threshold=0.05,
            triggered_by="pytest",
            started_at=datetime.utcnow(),
        )
        db_session.add_all([r1, r2])
        db_session.flush()

        j1 = JobInstance(
            workflow_run_id=r1.id,
            task_template_id=t1.id,
            device_id=sample_device.id,
            host_id=sample_device.host_id,
            status="COMPLETED",
            status_reason=None,
            pipeline_def=t1.pipeline_def,
            started_at=datetime.utcnow(),
            ended_at=datetime.utcnow(),
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        j2 = JobInstance(
            workflow_run_id=r2.id,
            task_template_id=t2.id,
            device_id=sample_device.id,
            host_id=sample_device.host_id,
            status="RUNNING",
            status_reason="executing",
            pipeline_def=t2.pipeline_def,
            started_at=datetime.utcnow(),
            ended_at=None,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        db_session.add_all([j1, j2])
        db_session.commit()

        response = client.get("/api/v1/tasks/0/runs", params={"skip": 0, "limit": 50})
        assert response.status_code == 200
        payload = response.json()
        assert "items" in payload
        assert "total" in payload
        assert payload["total"] >= 2

        run_map = {item["id"]: item for item in payload["items"]}
        assert j1.id in run_map
        assert j2.id in run_map

        assert run_map[j1.id]["task_id"] == wf1.id
        assert run_map[j1.id]["status"] == "FINISHED"
        assert run_map[j1.id]["progress"] == 100
        assert run_map[j2.id]["task_id"] == wf2.id
        assert run_map[j2.id]["status"] == "RUNNING"
        assert run_map[j2.id]["progress"] == 0


class TestDispatchTask:
    """Test POST /api/v1/tasks/{task_id}/dispatch (legacy endpoint now migrated)"""

    def test_dispatch_task_returns_503(self, client, sample_host, sample_device, auth_headers):
        response = client.post(
            "/api/v1/tasks/1/dispatch",
            json={
                "host_id": sample_host.id,
                "device_id": sample_device.id,
            },
            headers=auth_headers,
        )
        assert response.status_code == 503
        assert "迁移" in response.json()["detail"]


class TestTaskTemplates:
    """Test GET /api/v1/task-templates"""

    def test_list_task_templates(self, client):
        response = client.get("/api/v1/task-templates")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) > 0

    def test_task_template_structure(self, client):
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


class TestRunReportFromJobChain:
    """Validate /runs/{id}/report* can read new Job chain completion snapshot."""

    def test_get_run_report_from_job_snapshot(self, client, db_session, sample_device, tmp_path):
        now = datetime.utcnow()

        wf = WorkflowDefinition(
            name="job-report-workflow",
            description="report from job chain",
            failure_threshold=0.05,
            created_at=now,
            updated_at=now,
        )
        db_session.add(wf)
        db_session.flush()

        template = TaskTemplate(
            workflow_definition_id=wf.id,
            name="default",
            pipeline_def={"stages": {"prepare": [], "execute": [], "post_process": []}},
            sort_order=0,
            created_at=now,
        )
        db_session.add(template)
        db_session.flush()

        wf_run = WorkflowRun(
            workflow_definition_id=wf.id,
            status="SUCCESS",
            failure_threshold=0.05,
            triggered_by="pytest",
            started_at=now,
            ended_at=now,
        )
        db_session.add(wf_run)
        db_session.flush()

        job = JobInstance(
            workflow_run_id=wf_run.id,
            task_template_id=template.id,
            device_id=sample_device.id,
            host_id=sample_device.host_id,
            status="COMPLETED",
            status_reason=None,
            pipeline_def={"stages": {"prepare": [], "execute": [], "post_process": []}},
            started_at=now,
            ended_at=now,
            created_at=now,
            updated_at=now,
        )
        db_session.add(job)
        db_session.flush()
        job_id = job.id
        wf_id = wf.id

        risk_path = tmp_path / "risk_summary.json"
        risk_path.write_text(
            json.dumps(
                {
                    "risk_level": "HIGH",
                    "counts": {
                        "events_total": 3,
                        "restart_count": 2,
                        "aee_entries": 1,
                        "by_type": {"ANR": 1, "CRASH": 1},
                    },
                }
            ),
            encoding="utf-8",
        )

        snapshot = StepTrace(
            job_id=job.id,
            step_id="__job__",
            stage="post_process",
            status="COMPLETED",
            event_type="RUN_COMPLETE",
            output=json.dumps(
                {
                    "update": {
                        "status": "FINISHED",
                        "exit_code": 0,
                        "error_code": None,
                        "error_message": None,
                        "log_summary": "risk=HIGH;restarts=2;events=3",
                    },
                    "artifact": {
                        "storage_uri": f"file://{risk_path}",
                        "size_bytes": risk_path.stat().st_size,
                        "checksum": "pytest",
                    },
                }
            ),
            error_message=None,
            original_ts=now,
            created_at=now,
        )
        db_session.add(snapshot)
        db_session.commit()

        response = client.get(f"/api/v1/runs/{job_id}/report")
        assert response.status_code == 200
        data = response.json()
        assert data["run"]["id"] == job_id
        assert data["task"]["id"] == wf_id
        assert data["task"]["type"] == "WORKFLOW"
        assert data["summary_metrics"]["restarts"] == 2
        assert data["risk_summary"]["risk_level"] == "HIGH"
        assert len(data["run"]["artifacts"]) == 1

        cached_response = client.get(f"/api/v1/runs/{job_id}/report/cached")
        assert cached_response.status_code == 200
        cached_data = cached_response.json()
        assert cached_data["run"]["id"] == job_id
        assert cached_data["summary_metrics"]["restarts"] == 2
