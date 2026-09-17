"""
Tests for run-oriented API routes after removing the legacy /tasks* compatibility layer.
"""

import json
from datetime import datetime, timezone

from backend.models.job import JobInstance, JobLogSignal, StepTrace
from backend.models.plan import Plan
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
        assert cached_data["data"]["run"]["id"] == job_id
        assert cached_data["data"]["summary_metrics"]["restarts"] == 2


class TestRunJiraDraftProjectKey:
    """ADR-0029 P0：草稿端点解析 plan_run.project_id 快照 → 项目 jira 键。

    与提单（dedup）同口径：Plan 事后改归属不影响历史 Run 的 JIRA 目标。
    """

    def _seed_job(self, db_session, sample_device, *, project_jira_key=None, plan_run_project_id=None):
        from backend.models.project import TestProject

        now = datetime.now(timezone.utc)
        project = None
        if plan_run_project_id is not None:
            project = TestProject(
                project_key="DRAFT-P", display_name="draft proj",
                jira_project_key=project_jira_key,
            )
            db_session.add(project)
            db_session.flush()
        plan = Plan(name="draft-workflow", failure_threshold=0.05)
        db_session.add(plan)
        db_session.flush()
        plan_run = PlanRun(
            plan_id=plan.id,
            # 传入 plan_run_project_id 时用新建项目的真实 id（硬编码数字会
            # 撞上 GENERIC 哨兵占位 id）
            project_id=(project.id if project else plan_run_project_id),
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
            pipeline_def={"stages": {"prepare": [], "execute": [], "post_process": []}},
            started_at=now, ended_at=now, created_at=now, updated_at=now,
        )
        db_session.add(job)
        db_session.flush()
        snapshot = StepTrace(
            job_id=job.id,
            step_id="__job__",
            stage="post_process",
            status="COMPLETED",
            event_type="RUN_COMPLETE",
            output=json.dumps({"update": {"status": "FINISHED", "exit_code": 0}}),
            original_ts=now,
            created_at=now,
        )
        db_session.add(snapshot)
        db_session.commit()
        return job.id

    def test_draft_uses_plan_run_project_key(
        self, client, auth_headers, db_session, sample_device,
    ):
        job_id = self._seed_job(
            db_session, sample_device,
            plan_run_project_id=1, project_jira_key="V552AA-VFFB",
        )
        resp = client.post(f"/api/v1/runs/{job_id}/jira-draft", headers=auth_headers)
        assert resp.status_code == 200, resp.text
        draft = resp.json()
        assert draft["project_key"] == "V552AA-VFFB"
        assert draft["extra"]["project_key_source"] == "plan_run_project"

    def test_draft_without_project_marks_global_default(
        self, client, auth_headers, db_session, sample_device,
    ):
        job_id = self._seed_job(db_session, sample_device)
        resp = client.post(f"/api/v1/runs/{job_id}/jira-draft", headers=auth_headers)
        assert resp.status_code == 200, resp.text
        draft = resp.json()
        assert draft["project_key"] == "STABILITY"
        assert draft["extra"]["project_key_source"] == "global_default"
        assert draft["extra"]["project_key_global_default"] == "STABILITY"


class TestReportPlanRunOwnership:
    """#2420 第 2 项：报告端点的可选归属校验（URL 里的 id 是 job id，过去不校验配对）。"""

    @staticmethod
    def _seed(db_session, sample_device):
        from datetime import datetime, timedelta, timezone

        from backend.models.host import Host
        from backend.models.plan import Plan
        from backend.models.plan_run import PlanRun

        now = datetime.now(timezone.utc)
        host = db_session.get(Host, sample_device.host_id) or Host(
            id=sample_device.host_id, hostname="own-check", name="own-check",
            ip="192.0.2.10", ip_address="192.0.2.10", status="ONLINE",
            ssh_port=22, ssh_user="root", ssh_auth_type="password",
        )
        if db_session.get(Host, sample_device.host_id) is None:
            db_session.add(host)
            db_session.commit()
        plans, runs = [], []
        for tag in ("A", "B"):
            plan = Plan(name=f"own-{tag}", description="", failure_threshold=0.05)
            db_session.add(plan)
            db_session.flush()
            run = PlanRun(
                plan_id=plan.id, status="SUCCESS", failure_threshold=0.05,
                plan_snapshot={"name": plan.name, "plan_id": plan.id}, run_type="MANUAL",
                triggered_by="pytest",
                started_at=now - timedelta(minutes=30), ended_at=now - timedelta(minutes=20),
            )
            db_session.add(run)
            db_session.flush()
            job = JobInstance(
                plan_run_id=run.id, plan_id=plan.id, device_id=sample_device.id,
                host_id=sample_device.host_id, status="COMPLETED", status_reason=None,
                pipeline_def={"lifecycle": {"init": [], "teardown": []}},
                started_at=now - timedelta(minutes=25), ended_at=now - timedelta(minutes=21),
            )
            db_session.add(job)
            db_session.commit()
            plans.append(plan)
            runs.append(run)
            if tag == "A":
                job_a = job
        return runs[0], job_a

    def test_mismatched_plan_run_is_404_with_code(
        self, client, auth_headers, db_session, sample_device
    ):
        """错配必须 404，并且**不能**返回另一个 run 的 job 报告（旧行为）。"""
        run_a, job_a = self._seed(db_session, sample_device)
        other_run_id = run_a.id + 10_000  # 不存在的 run：更该拒

        resp = client.get(
            f"/api/v1/runs/{job_a.id}/report",
            params={"plan_run_id": other_run_id},
            headers=auth_headers,
        )
        assert resp.status_code == 404, resp.text
        detail = resp.json()["detail"]
        assert isinstance(detail, dict) and detail["code"] == "job_not_in_plan_run", detail

    def test_matching_plan_run_passes(
        self, client, auth_headers, db_session, sample_device
    ):
        run_a, job_a = self._seed(db_session, sample_device)
        resp = client.get(
            f"/api/v1/runs/{job_a.id}/report",
            params={"plan_run_id": run_a.id},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["run"]["id"] == job_a.id

    def test_without_param_keeps_legacy_behaviour(
        self, client, auth_headers, db_session, sample_device
    ):
        """不传参数保持原行为：老脚本与历史深链不被本单收紧打断。"""
        _, job_a = self._seed(db_session, sample_device)
        resp = client.get(
            f"/api/v1/runs/{job_a.id}/report", headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text

    def test_cached_endpoint_also_checks_ownership(
        self, client, auth_headers, db_session, sample_device
    ):
        """三个报告端点同源同校验——漏一个就等于"校验可选且看运气"。"""
        run_a, job_a = self._seed(db_session, sample_device)
        resp = client.get(
            f"/api/v1/runs/{job_a.id}/report/cached",
            params={"plan_run_id": run_a.id + 10_000},
            headers=auth_headers,
        )
        assert resp.status_code == 404, resp.text
        assert resp.json()["detail"]["code"] == "job_not_in_plan_run"
