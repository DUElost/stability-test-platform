"""
Tests for run-oriented API routes after removing the legacy /tasks* compatibility layer.
"""

import json
from datetime import datetime, timezone

from backend.models.job import JobInstance, JobLogSignal, StepTrace
from backend.models.plan import Plan, PlanStep
from backend.models.plan_run import PlanRun


class TestRunReportFromJobChain:
    """Validate /runs/{id}/report* can read new Job chain completion snapshot."""

    def test_get_run_report_from_job_snapshot(self, client, auth_headers, db_session, sample_device, tmp_path):
        now = datetime.now(timezone.utc)

        plan = Plan(
            name="job-report-workflow",
            description="report from job chain",
            failure_threshold=0.05,
                    )
        db_session.add(plan)
        db_session.flush()

        plan_run = PlanRun(
            plan_id=plan.id,
            status="SUCCESS",
            failure_threshold=0.05,
            plan_snapshot={"name": plan.name, "plan_id": plan.id},
            run_type="MANUAL",
            triggered_by="pytest",
        )
        db_session.add(plan_run)
        db_session.flush()

        job = JobInstance(
            plan_run_id=plan_run.id,
            plan_id=plan.id,
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
        plan_id = plan.id

        for i in range(10):
            sig = JobLogSignal(
                job_id=job.id,
                host_id=str(sample_device.host_id),
                device_serial=sample_device.serial,
                seq_no=i,
                category="ANR",
                source="inotifyd",
                path_on_device=f"/data/anr/traces_{i}.txt",
                artifact_uri=None,
                sha256=None,
                size_bytes=None,
                first_lines="ANR in com.example",
                detected_at=now,
                received_at=now,
                extra={"event_subtype": "ANR", "nfs_path": f"/nfs/anr/traces_{i}", "schema_version": 2},
            )
            db_session.add(sig)

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
                }
            ),
            error_message=None,
            original_ts=now,
            created_at=now,
        )
        db_session.add(snapshot)
        db_session.commit()

        response = client.get(f"/api/v1/runs/{job_id}/report", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["run"]["id"] == job_id
        assert data["task"]["id"] == plan_id
        assert data["task"]["type"] == "PLAN"
        assert data["summary_metrics"]["restarts"] == 2
        assert data["risk_summary"]["risk_level"] == "A"
        assert data["risk_summary"]["counts"]["by_type"]["ANR"] == 10

        cached_response = client.get(f"/api/v1/runs/{job_id}/report/cached", headers=auth_headers)
        assert cached_response.status_code == 200
        cached_data = cached_response.json()
        assert cached_data["run"]["id"] == job_id
        assert cached_data["summary_metrics"]["restarts"] == 2
