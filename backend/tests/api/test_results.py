"""Tests for results API routes"""

import json
from datetime import datetime, timedelta, timezone

from backend.models.host import Device
from backend.models.job import JobInstance, StepTrace
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun


class TestResultsSummary:
    def test_summary_empty(self, client, auth_headers):
        response = client.get("/api/v1/results/summary", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert "runs_by_status" in data
        assert "test_type_stats" in data
        assert "risk_distribution" in data
        assert "recent_runs" in data
        assert data["runs_by_status"]["total"] >= 0

    def test_summary_with_limit(self, client, auth_headers):
        response = client.get("/api/v1/results/summary", params={"limit": 5}, headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert len(data["recent_runs"]) <= 5

    def test_summary_aggregates_from_job_instance_chain(self, client, auth_headers, db_session, sample_device):
        now = datetime.now(timezone.utc)
        baseline = client.get("/api/v1/results/summary", headers=auth_headers).json()
        suffix = now.strftime("%Y%m%d%H%M%S%f")
        smoke_type = f"Smoke-{suffix}"
        stress_type = f"Stress-{suffix}"

        plan_smoke = Plan(
            name=smoke_type,
            description="",
            failure_threshold=0.05,
                    )
        plan_stress = Plan(
            name=stress_type,
            description="",
            failure_threshold=0.05,
                    )
        db_session.add_all([plan_smoke, plan_stress])
        db_session.flush()

        plan_run = PlanRun(
            plan_id=plan_smoke.id,
            status="RUNNING",
            failure_threshold=0.05,
            plan_snapshot={"name": plan_smoke.name, "plan_id": plan_smoke.id},
            run_type="MANUAL",
            triggered_by="pytest",
        )
        db_session.add(plan_run)
        db_session.flush()

        pipeline_def = {"lifecycle": {"init": [], "teardown": []}}
        devices = [sample_device]
        for index in range(3):
            device = Device(
                serial=f"RESULT-{suffix}-{index}",
                host_id=sample_device.host_id,
                status="ONLINE",
            )
            db_session.add(device)
            devices.append(device)
        db_session.flush()

        jobs = [
            JobInstance(
                plan_run_id=plan_run.id,
                plan_id=plan_smoke.id,
                device_id=devices[0].id,
                host_id=sample_device.host_id,
                status="COMPLETED",
                status_reason=None,
                pipeline_def=pipeline_def,
                started_at=now - timedelta(minutes=12),
                ended_at=now - timedelta(minutes=10),
                created_at=now - timedelta(minutes=12),
                updated_at=now - timedelta(minutes=10),
            ),
            JobInstance(
                plan_run_id=plan_run.id,
                plan_id=plan_stress.id,
                device_id=devices[1].id,
                host_id=sample_device.host_id,
                status="FAILED",
                status_reason="tool failed",
                pipeline_def=pipeline_def,
                started_at=now - timedelta(minutes=9),
                ended_at=now - timedelta(minutes=8),
                created_at=now - timedelta(minutes=9),
                updated_at=now - timedelta(minutes=8),
            ),
            JobInstance(
                plan_run_id=plan_run.id,
                plan_id=plan_stress.id,
                device_id=devices[2].id,
                host_id=sample_device.host_id,
                status="ABORTED",
                status_reason="manual stop",
                pipeline_def=pipeline_def,
                started_at=now - timedelta(minutes=7),
                ended_at=now - timedelta(minutes=7),
                created_at=now - timedelta(minutes=7),
                updated_at=now - timedelta(minutes=7),
            ),
            JobInstance(
                plan_run_id=plan_run.id,
                plan_id=plan_smoke.id,
                device_id=devices[3].id,
                host_id=sample_device.host_id,
                status="RUNNING",
                status_reason=None,
                pipeline_def=pipeline_def,
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

        response = client.get("/api/v1/results/summary", params={"limit": 3}, headers=auth_headers)
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
        assert smoke_type in data["recent_runs"][0]["task_name"]
        assert data["recent_runs"][1]["run_id"] == jobs[2].id
        assert data["recent_runs"][1]["status"] == "CANCELED"


class TestRiskTrend:
    """ADR-0029 P2：项目级风险趋势（按天 S/A/B，run 级 DLE 权威聚合）。"""

    def test_trend_empty(self, client, auth_headers):
        resp = client.get("/api/v1/results/risk-trend", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["buckets"] == []
        assert data["days"] == 30

    def test_trend_buckets_by_project(
        self, client, auth_headers, db_session, sample_device,
    ):
        from backend.models.job import JobLogSignal
        from backend.models.project import TestProject

        now = datetime.now(timezone.utc)
        project = TestProject(project_key="TREND-A", display_name="trend",
                              source="USER")
        db_session.add(project)
        db_session.flush()
        plan = Plan(name="trend-plan", failure_threshold=0.05, project_id=project.id)
        db_session.add(plan)
        db_session.flush()
        run = PlanRun(
            plan_id=plan.id,
            project_id=project.id,
            status="SUCCESS",
            failure_threshold=0.05,
            plan_snapshot={"name": plan.name, "plan_id": plan.id},
            run_type="MANUAL",
            triggered_by="pytest",
            started_at=now - timedelta(days=1),
        )
        db_session.add(run)
        db_session.flush()
        job = JobInstance(
            plan_run_id=run.id,
            plan_id=plan.id,
            device_id=sample_device.id,
            host_id=sample_device.host_id,
            status="COMPLETED",
            pipeline_def={"lifecycle": {"init": [], "teardown": []}},
            started_at=now - timedelta(days=1),
            ended_at=now - timedelta(days=1),
            created_at=now - timedelta(days=1),
            updated_at=now - timedelta(days=1),
        )
        db_session.add(job)
        db_session.flush()
        # S 级：category 在白名单（ANR）+ event_subtype=swt（命中 swt 规则）
        # + nfs_path 非空（unlinked 计数按 nfs_path DISTINCT）
        db_session.add(JobLogSignal(
            job_id=job.id,
            host_id=str(sample_device.host_id),
            device_serial=sample_device.serial,
            seq_no=0,
            category="ANR",
            source="inotifyd",
            path_on_device="/data/anr/1.txt",
            detected_at=now - timedelta(days=1),
            received_at=now - timedelta(days=1),
            extra={
                "event_subtype": "swt",
                "nfs_path": "/nfs/swt/1",
                "schema_version": 2,
            },
        ))
        db_session.commit()

        resp = client.get(
            "/api/v1/results/risk-trend?project_key=TREND-A",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["project_key"] == "TREND-A"
        assert len(data["buckets"]) == 1
        bucket = data["buckets"][0]
        assert bucket["date"] == (now - timedelta(days=1)).date().isoformat()
        assert bucket["S"] == 1
        assert bucket["runs"] == 1

        # 其他项目不串扰
        other = client.get(
            "/api/v1/results/risk-trend?project_key=NOPE",
            headers=auth_headers,
        )
        assert other.status_code == 404
