"""Tests for results API routes"""

import json
from datetime import datetime, timedelta

from backend.models.job import JobInstance, StepTrace, TaskTemplate
from backend.models.workflow import WorkflowDefinition, WorkflowRun


class TestResultsSummary:
    def test_summary_empty(self, client):
        response = client.get("/api/v1/results/summary")
        assert response.status_code == 200
        data = response.json()
        assert "runs_by_status" in data
        assert "test_type_stats" in data
        assert "risk_distribution" in data
        assert "recent_runs" in data
        assert data["runs_by_status"]["total"] >= 0

    def test_summary_with_limit(self, client):
        response = client.get("/api/v1/results/summary", params={"limit": 5})
        assert response.status_code == 200
        data = response.json()
        assert len(data["recent_runs"]) <= 5

    def test_summary_aggregates_from_job_instance_chain(self, client, db_session, sample_device):
        now = datetime.utcnow()
        baseline = client.get("/api/v1/results/summary").json()
        suffix = now.strftime("%Y%m%d%H%M%S%f")
        smoke_type = f"Smoke-{suffix}"
        stress_type = f"Stress-{suffix}"

        wf = WorkflowDefinition(
            name="results-wf",
            description="results aggregation",
            failure_threshold=0.05,
            created_at=now,
            updated_at=now,
        )
        db_session.add(wf)
        db_session.flush()

        tpl_smoke = TaskTemplate(
            workflow_definition_id=wf.id,
            name=smoke_type,
            pipeline_def={"stages": {"prepare": [], "execute": [], "post_process": []}},
            sort_order=0,
            created_at=now,
        )
        tpl_stress = TaskTemplate(
            workflow_definition_id=wf.id,
            name=stress_type,
            pipeline_def={"stages": {"prepare": [], "execute": [], "post_process": []}},
            sort_order=1,
            created_at=now,
        )
        db_session.add_all([tpl_smoke, tpl_stress])
        db_session.flush()

        wf_run = WorkflowRun(
            workflow_definition_id=wf.id,
            status="RUNNING",
            failure_threshold=0.05,
            triggered_by="pytest",
            started_at=now,
            ended_at=None,
            result_summary=None,
        )
        db_session.add(wf_run)
        db_session.flush()

        jobs = [
            JobInstance(
                workflow_run_id=wf_run.id,
                task_template_id=tpl_smoke.id,
                device_id=sample_device.id,
                host_id=sample_device.host_id,
                status="COMPLETED",
                status_reason=None,
                pipeline_def=tpl_smoke.pipeline_def,
                started_at=now - timedelta(minutes=12),
                ended_at=now - timedelta(minutes=10),
                created_at=now - timedelta(minutes=12),
                updated_at=now - timedelta(minutes=10),
            ),
            JobInstance(
                workflow_run_id=wf_run.id,
                task_template_id=tpl_stress.id,
                device_id=sample_device.id,
                host_id=sample_device.host_id,
                status="FAILED",
                status_reason="tool failed",
                pipeline_def=tpl_stress.pipeline_def,
                started_at=now - timedelta(minutes=9),
                ended_at=now - timedelta(minutes=8),
                created_at=now - timedelta(minutes=9),
                updated_at=now - timedelta(minutes=8),
            ),
            JobInstance(
                workflow_run_id=wf_run.id,
                task_template_id=tpl_stress.id,
                device_id=sample_device.id,
                host_id=sample_device.host_id,
                status="ABORTED",
                status_reason="manual stop",
                pipeline_def=tpl_stress.pipeline_def,
                started_at=now - timedelta(minutes=7),
                ended_at=now - timedelta(minutes=7),
                created_at=now - timedelta(minutes=7),
                updated_at=now - timedelta(minutes=7),
            ),
            JobInstance(
                workflow_run_id=wf_run.id,
                task_template_id=tpl_smoke.id,
                device_id=sample_device.id,
                host_id=sample_device.host_id,
                status="RUNNING",
                status_reason=None,
                pipeline_def=tpl_smoke.pipeline_def,
                started_at=now - timedelta(minutes=3),
                ended_at=None,
                created_at=now - timedelta(minutes=3),
                updated_at=now - timedelta(minutes=2),
            ),
        ]
        db_session.add_all(jobs)
        db_session.flush()

        db_session.add_all([
            StepTrace(
                job_id=jobs[0].id,
                step_id="__job__",
                stage="post_process",
                status="COMPLETED",
                event_type="RUN_COMPLETE",
                output=json.dumps({"update": {"log_summary": "risk=HIGH;restarts=2"}}),
                error_message=None,
                original_ts=now - timedelta(minutes=10),
                created_at=now - timedelta(minutes=10),
            ),
            StepTrace(
                job_id=jobs[1].id,
                step_id="__job__",
                stage="post_process",
                status="FAILED",
                event_type="RUN_COMPLETE",
                output=json.dumps({"update": {"log_summary": "risk=LOW;restarts=1"}}),
                error_message="boom",
                original_ts=now - timedelta(minutes=8),
                created_at=now - timedelta(minutes=8),
            ),
        ])
        db_session.commit()

        response = client.get("/api/v1/results/summary", params={"limit": 3})
        assert response.status_code == 200
        data = response.json()

        assert data["runs_by_status"]["finished"] == baseline["runs_by_status"]["finished"] + 1
        assert data["runs_by_status"]["failed"] == baseline["runs_by_status"]["failed"] + 1
        assert data["runs_by_status"]["canceled"] == baseline["runs_by_status"]["canceled"] + 1
        assert data["runs_by_status"]["running"] == baseline["runs_by_status"]["running"] + 1
        assert data["runs_by_status"]["total"] == baseline["runs_by_status"]["total"] + 4

        type_stats = {row["type"]: row for row in data["test_type_stats"]}
        assert type_stats[smoke_type]["total"] == 2
        assert type_stats[smoke_type]["finished"] == 1
        assert type_stats[smoke_type]["failed"] == 0
        assert type_stats[stress_type]["total"] == 2
        assert type_stats[stress_type]["finished"] == 0
        assert type_stats[stress_type]["failed"] == 1

        assert data["risk_distribution"]["high"] == baseline["risk_distribution"]["high"] + 1
        assert data["risk_distribution"]["medium"] == baseline["risk_distribution"]["medium"]
        assert data["risk_distribution"]["low"] == baseline["risk_distribution"]["low"] + 1
        assert data["risk_distribution"]["unknown"] == baseline["risk_distribution"]["unknown"] + 2

        assert len(data["recent_runs"]) == 3
        assert data["recent_runs"][0]["run_id"] == jobs[3].id
        assert data["recent_runs"][0]["status"] == "RUNNING"
        assert data["recent_runs"][0]["task_name"] == f"results-wf/{smoke_type}"
        assert data["recent_runs"][1]["run_id"] == jobs[2].id
        assert data["recent_runs"][1]["status"] == "CANCELED"
