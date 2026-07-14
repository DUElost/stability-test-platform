"""Tests for stats API routes"""

from datetime import datetime, timedelta, timezone

from backend.models.enums import HostStatus
from backend.models.host import Host, Device
from backend.models.job import JobInstance
from backend.models.plan import Plan, PlanStep
from backend.models.plan_run import PlanRun


class TestDashboardSummary:
    def test_dashboard_summary_empty(self, client, auth_headers):
        response = client.get("/api/v1/stats/dashboard-summary", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()

        assert data["hosts"] == {
            "total": 0,
            "online": 0,
            "offline": 0,
            "degraded": 0,
            "avg_cpu_load": 0.0,
            "avg_ram_usage": 0.0,
            "avg_disk_usage": 0.0,
            "online_rate": 0.0,
        }
        assert data["devices"] == {
            "total": 0,
            "idle": 0,
            "testing": 0,
            "offline": 0,
            "error": 0,
            "low_battery": 0,
            "high_temp": 0,
        }
        assert data["alerts"] == {
            "total": 0,
            "low_battery": 0,
            "high_temp": 0,
            "error": 0,
        }
        assert data["host_resources"] == []

    def test_dashboard_summary_aggregates_hosts_devices_and_alerts(
        self, client, auth_headers, db_session, sample_host,
    ):
        sample_host.status = HostStatus.ONLINE.value
        sample_host.last_heartbeat = datetime.now(timezone.utc)
        sample_host.extra = {
            "cpu_load": 12.5,
            "ram_usage": 48.0,
            "disk_usage": {"usage_percent": 77.7},
        }

        device_idle = Device(
            serial="DEV-IDLE-1",
            status="ONLINE",
            host_id=sample_host.id,
            battery_level=10,
            temperature=46,
        )
        device_busy = Device(
            serial="DEV-BUSY-1",
            status="BUSY",
            host_id=sample_host.id,
            battery_level=88,
            temperature=32,
        )
        device_offline = Device(
            serial="DEV-OFF-1",
            status="OFFLINE",
            host_id=sample_host.id,
            battery_level=15,
            temperature=50,
        )
        db_session.add_all([device_idle, device_busy, device_offline])
        db_session.commit()

        response = client.get("/api/v1/stats/dashboard-summary", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()

        assert data["hosts"]["total"] == 1
        assert data["hosts"]["online"] == 1
        assert data["hosts"]["avg_cpu_load"] == 12.5
        assert data["devices"]["total"] == 3
        assert data["devices"]["idle"] == 1
        assert data["devices"]["testing"] == 1
        assert data["devices"]["offline"] == 1
        assert data["devices"]["low_battery"] == 2
        assert data["devices"]["high_temp"] == 2
        assert data["alerts"]["total"] == 4
        assert data["host_resources"][0]["ip"] == "172.21.15.100"

    def test_null_battery_not_counted_as_low(self, client, auth_headers, db_session, sample_host):
        """回归: NULL 电量不应被误算为低电量告警"""
        device_no_battery = Device(
            serial="DEV-NULL-BATT",
            status="ONLINE",
            host_id=sample_host.id,
            battery_level=None,
            temperature=None,
        )
        db_session.add(device_no_battery)
        db_session.commit()

        response = client.get("/api/v1/stats/dashboard-summary", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["devices"]["total"] == 1
        assert data["devices"]["low_battery"] == 0
        assert data["devices"]["high_temp"] == 0
        assert data["alerts"]["total"] == 0


class TestActivityStats:
    def test_activity_default(self, client, auth_headers):
        response = client.get("/api/v1/stats/activity", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert "points" in data
        assert "hours" in data

    def test_activity_custom_hours(self, client, auth_headers):
        response = client.get("/api/v1/stats/activity", params={"hours": 48}, headers=auth_headers)
        assert response.status_code == 200
        assert response.json()["hours"] == 48

    def test_activity_excludes_hidden_legacy_aee_plan_jobs(
        self, client, auth_headers, db_session, sample_device,
    ):
        now = datetime.now(timezone.utc)
        hidden_plan = Plan(
            name="Hidden Legacy Activity Plan",
            description="",
            failure_threshold=0.05,
        )
        db_session.add(hidden_plan)
        db_session.flush()
        db_session.add_all([
            PlanStep(
                plan_id=hidden_plan.id,
                step_key="init_0",
                script_name="check_device",
                script_version="1.0.0",
                stage="init",
                sort_order=0,
            ),
            PlanStep(
                plan_id=hidden_plan.id,
                step_key="scan",
                script_name="scan_aee",
                script_version="1.0.0",
                stage="patrol",
                sort_order=1,
            ),
        ])

        hidden_plan_run = PlanRun(
            plan_id=hidden_plan.id,
            status="RUNNING",
            failure_threshold=0.05,
            plan_snapshot={"name": hidden_plan.name, "plan_id": hidden_plan.id},
            run_type="MANUAL",
            triggered_by="pytest",
        )
        db_session.add(hidden_plan_run)
        db_session.flush()

        db_session.add(JobInstance(
            plan_run_id=hidden_plan_run.id,
            plan_id=hidden_plan.id,
            device_id=sample_device.id,
            host_id=sample_device.host_id,
            status="COMPLETED",
            status_reason=None,
            pipeline_def={"lifecycle": {"init": [], "teardown": []}},
            started_at=now - timedelta(minutes=30),
            ended_at=now - timedelta(minutes=29),
            created_at=now - timedelta(minutes=30),
            updated_at=now - timedelta(minutes=29),
        ))
        db_session.commit()

        response = client.get("/api/v1/stats/activity", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()

        assert sum(point["started"] for point in data["points"]) == 0
        assert sum(point["completed"] for point in data["points"]) == 0
        assert sum(point["failed"] for point in data["points"]) == 0


class TestCompletionTrend:
    def test_completion_trend_default(self, client, auth_headers):
        response = client.get("/api/v1/stats/completion-trend", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert "points" in data
        assert "days" in data

    def test_completion_trend_custom_days(self, client, auth_headers):
        response = client.get("/api/v1/stats/completion-trend", params={"days": 14}, headers=auth_headers)
        assert response.status_code == 200
        assert response.json()["days"] == 14

    def test_completion_trend_excludes_hidden_legacy_aee_plan_jobs(
        self, client, auth_headers, db_session, sample_device,
    ):
        now = datetime.now(timezone.utc)
        hidden_plan = Plan(
            name="Hidden Legacy Completion Plan",
            description="",
            failure_threshold=0.05,
        )
        db_session.add(hidden_plan)
        db_session.flush()
        db_session.add_all([
            PlanStep(
                plan_id=hidden_plan.id,
                step_key="init_0",
                script_name="check_device",
                script_version="1.0.0",
                stage="init",
                sort_order=0,
            ),
            PlanStep(
                plan_id=hidden_plan.id,
                step_key="export",
                script_name="export_mobilelogs",
                script_version="1.0.0",
                stage="teardown",
                sort_order=1,
            ),
        ])

        hidden_plan_run = PlanRun(
            plan_id=hidden_plan.id,
            status="RUNNING",
            failure_threshold=0.05,
            plan_snapshot={"name": hidden_plan.name, "plan_id": hidden_plan.id},
            run_type="MANUAL",
            triggered_by="pytest",
        )
        db_session.add(hidden_plan_run)
        db_session.flush()

        db_session.add(JobInstance(
            plan_run_id=hidden_plan_run.id,
            plan_id=hidden_plan.id,
            device_id=sample_device.id,
            host_id=sample_device.host_id,
            status="FAILED",
            status_reason=None,
            pipeline_def={"lifecycle": {"init": [], "teardown": []}},
            started_at=now - timedelta(hours=2),
            ended_at=now - timedelta(hours=1),
            created_at=now - timedelta(hours=2),
            updated_at=now - timedelta(hours=1),
        ))
        db_session.commit()

        response = client.get("/api/v1/stats/completion-trend", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()

        assert sum(point["passed"] for point in data["points"]) == 0
        assert sum(point["failed"] for point in data["points"]) == 0


def _make_plan_run(db_session, plan_id: str | int, *, status: str = "SUCCESS") -> "PlanRun":
    plan_run = PlanRun(
        plan_id=plan_id,
        status=status,
        failure_threshold=0.05,
        plan_snapshot={"plan_id": plan_id},
        run_type="MANUAL",
        triggered_by="pytest",
    )
    db_session.add(plan_run)
    db_session.flush()
    return plan_run


def _make_device(db_session, host_id: str, serial: str) -> Device:
    device = Device(serial=serial, host_id=host_id, status="ONLINE")
    db_session.add(device)
    db_session.flush()
    return device


class TestHostFailureRate:
    def test_empty(self, client, auth_headers):
        response = client.get("/api/v1/stats/host-failure-rate", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data == {"items": [], "days": 30}

    def test_aggregates_failure_rate_per_host(
        self, client, auth_headers, db_session, sample_host, sample_device,
    ):
        now = datetime.now(timezone.utc)
        plan = Plan(name="host-failure-plan", description="", failure_threshold=0.05)
        db_session.add(plan)
        db_session.flush()
        plan_run = _make_plan_run(db_session, plan.id)

        devices = [
            sample_device,
            _make_device(db_session, sample_host.id, "HOST-RATE-2"),
            _make_device(db_session, sample_host.id, "HOST-RATE-3"),
        ]
        for status, device in zip(
            ("COMPLETED", "FAILED", "FAILED"), devices,
        ):
            db_session.add(JobInstance(
                plan_run_id=plan_run.id,
                plan_id=plan.id,
                device_id=device.id,
                host_id=sample_host.id,
                status=status,
                pipeline_def={"lifecycle": {"init": [], "teardown": []}},
                started_at=now - timedelta(minutes=10),
                ended_at=now - timedelta(minutes=9),
            ))
        db_session.commit()

        response = client.get("/api/v1/stats/host-failure-rate", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 1
        item = data["items"][0]
        assert item["host_id"] == sample_host.id
        assert item["total_jobs"] == 3
        assert item["failed"] == 2
        assert item["failure_rate"] == 0.6667

    def test_respects_limit_param(self, client, auth_headers, db_session, sample_device):
        now = datetime.now(timezone.utc)
        plan = Plan(name="host-failure-limit-plan", description="", failure_threshold=0.05)
        db_session.add(plan)
        db_session.flush()
        plan_run = _make_plan_run(db_session, plan.id)

        for i in range(3):
            host = Host(
                id=f"host-limit-{i}",
                hostname=f"host-limit-{i}",
                ip=f"10.0.1.{i}",
                ip_address=f"10.0.1.{i}",
                status=HostStatus.ONLINE.value,
                last_heartbeat=now,
            )
            db_session.add(host)
            db_session.flush()
            device = _make_device(
                db_session, host.id, f"HOST-LIMIT-DEVICE-{i}",
            )
            db_session.add(JobInstance(
                plan_run_id=plan_run.id,
                plan_id=plan.id,
                device_id=device.id,
                host_id=host.id,
                status="FAILED",
                pipeline_def={"lifecycle": {"init": [], "teardown": []}},
                started_at=now - timedelta(minutes=5),
                ended_at=now - timedelta(minutes=4),
            ))
        db_session.commit()

        response = client.get(
            "/api/v1/stats/host-failure-rate", params={"limit": 2}, headers=auth_headers,
        )
        assert response.status_code == 200
        assert len(response.json()["items"]) == 2

    def test_excludes_hidden_legacy_aee_plan_jobs(
        self, client, auth_headers, db_session, sample_host, sample_device,
    ):
        now = datetime.now(timezone.utc)
        hidden_plan = Plan(name="Hidden Legacy Host Plan", description="", failure_threshold=0.05)
        db_session.add(hidden_plan)
        db_session.flush()
        db_session.add(PlanStep(
            plan_id=hidden_plan.id,
            step_key="scan",
            script_name="scan_aee",
            script_version="1.0.0",
            stage="patrol",
            sort_order=0,
        ))
        plan_run = _make_plan_run(db_session, hidden_plan.id)
        db_session.add(JobInstance(
            plan_run_id=plan_run.id,
            plan_id=hidden_plan.id,
            device_id=sample_device.id,
            host_id=sample_host.id,
            status="FAILED",
            pipeline_def={"lifecycle": {"init": [], "teardown": []}},
            started_at=now - timedelta(minutes=10),
            ended_at=now - timedelta(minutes=9),
        ))
        db_session.commit()

        response = client.get("/api/v1/stats/host-failure-rate", headers=auth_headers)
        assert response.status_code == 200
        assert response.json()["items"] == []


class TestPlanSuccessRate:
    def test_empty(self, client, auth_headers):
        response = client.get("/api/v1/stats/plan-success-rate", headers=auth_headers)
        assert response.status_code == 200
        assert response.json() == {"items": [], "days": 30}

    def test_ranks_by_pass_rate_desc(
        self, client, auth_headers, db_session, sample_host, sample_device,
    ):
        now = datetime.now(timezone.utc)
        good_plan = Plan(name="good-plan", description="", failure_threshold=0.05)
        bad_plan = Plan(name="bad-plan", description="", failure_threshold=0.05)
        db_session.add_all([good_plan, bad_plan])
        db_session.flush()
        good_run = _make_plan_run(db_session, good_plan.id)
        bad_run = _make_plan_run(db_session, bad_plan.id, status="FAILED")
        second_device = _make_device(
            db_session, sample_host.id, "PLAN-RATE-DEVICE-2",
        )
        devices = [sample_device, second_device]

        # good_plan: 2/2 passed; bad_plan: 0/2 passed
        for status, device in zip(("COMPLETED", "COMPLETED"), devices):
            db_session.add(JobInstance(
                plan_run_id=good_run.id, plan_id=good_plan.id,
                device_id=device.id, host_id=sample_host.id,
                status=status, pipeline_def={"lifecycle": {"init": [], "teardown": []}},
                started_at=now - timedelta(minutes=10), ended_at=now - timedelta(minutes=9),
            ))
        for status, device in zip(("FAILED", "FAILED"), devices):
            db_session.add(JobInstance(
                plan_run_id=bad_run.id, plan_id=bad_plan.id,
                device_id=device.id, host_id=sample_host.id,
                status=status, pipeline_def={"lifecycle": {"init": [], "teardown": []}},
                started_at=now - timedelta(minutes=10), ended_at=now - timedelta(minutes=9),
            ))
        db_session.commit()

        response = client.get("/api/v1/stats/plan-success-rate", headers=auth_headers)
        assert response.status_code == 200
        items = response.json()["items"]
        assert len(items) == 2
        assert items[0]["plan_name"] == "good-plan"
        assert items[0]["pass_rate"] == 1.0
        assert items[1]["plan_name"] == "bad-plan"
        assert items[1]["pass_rate"] == 0.0

    def test_excludes_hidden_legacy_aee_plan_jobs(
        self, client, auth_headers, db_session, sample_host, sample_device,
    ):
        now = datetime.now(timezone.utc)
        hidden_plan = Plan(name="Hidden Legacy Success Plan", description="", failure_threshold=0.05)
        db_session.add(hidden_plan)
        db_session.flush()
        db_session.add(PlanStep(
            plan_id=hidden_plan.id,
            step_key="export",
            script_name="export_mobilelogs",
            script_version="1.0.0",
            stage="teardown",
            sort_order=0,
        ))
        plan_run = _make_plan_run(db_session, hidden_plan.id)
        db_session.add(JobInstance(
            plan_run_id=plan_run.id,
            plan_id=hidden_plan.id,
            device_id=sample_device.id,
            host_id=sample_host.id,
            status="COMPLETED",
            pipeline_def={"lifecycle": {"init": [], "teardown": []}},
            started_at=now - timedelta(minutes=10),
            ended_at=now - timedelta(minutes=9),
        ))
        db_session.commit()

        response = client.get("/api/v1/stats/plan-success-rate", headers=auth_headers)
        assert response.status_code == 200
        assert response.json()["items"] == []


class TestPlanRunPassRateTrend:
    def test_empty(self, client, auth_headers):
        response = client.get("/api/v1/stats/plan-run-pass-rate-trend", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert "points" in data
        assert data["days"] == 30
        # 空数据也应返回 [since, today] 区间内每天一个占位点
        assert len(data["points"]) >= 1
        assert all(p["run_count"] == 0 for p in data["points"])

    def test_custom_days_param(self, client, auth_headers):
        response = client.get(
            "/api/v1/stats/plan-run-pass-rate-trend", params={"days": 7}, headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["days"] == 7

    def test_aggregates_completed_plan_runs_by_day(
        self, client, auth_headers, db_session, sample_host, sample_device,
    ):
        now = datetime.now(timezone.utc)
        plan = Plan(name="trend-plan", description="", failure_threshold=0.05)
        db_session.add(plan)
        db_session.flush()

        plan_run = PlanRun(
            plan_id=plan.id,
            status="SUCCESS",
            failure_threshold=0.05,
            plan_snapshot={"plan_id": plan.id},
            run_type="MANUAL",
            triggered_by="pytest",
            ended_at=now,
        )
        db_session.add(plan_run)
        db_session.flush()
        second_device = _make_device(
            db_session, sample_host.id, "TREND-DEVICE-2",
        )

        for status, device in zip(
            ("COMPLETED", "FAILED"),
            (sample_device, second_device),
        ):
            db_session.add(JobInstance(
                plan_run_id=plan_run.id, plan_id=plan.id,
                device_id=device.id, host_id=sample_host.id,
                status=status, pipeline_def={"lifecycle": {"init": [], "teardown": []}},
                started_at=now - timedelta(minutes=10), ended_at=now - timedelta(minutes=9),
            ))
        db_session.commit()

        response = client.get("/api/v1/stats/plan-run-pass-rate-trend", headers=auth_headers)
        assert response.status_code == 200
        points = response.json()["points"]
        today_point = next(p for p in points if p["date"] == now.date().isoformat())
        assert today_point["run_count"] == 1
        assert today_point["avg_pass_rate"] == 0.5
