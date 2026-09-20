"""Tests for stats API routes"""

from datetime import datetime, timedelta, timezone

from backend.models.enums import HostStatus
from backend.models.host import Host, Device
from backend.models.job import JobInstance
from backend.models.plan import Plan
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
            "avg_disk_usage": None,
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
        assert data["hosts"]["avg_disk_usage"] == 77.7
        assert data["host_resources"][0]["disk_usage"] == 77.7
        assert data["devices"]["total"] == 3
        assert data["devices"]["idle"] == 1
        assert data["devices"]["testing"] == 1
        assert data["devices"]["offline"] == 1
        assert data["devices"]["low_battery"] == 2
        assert data["devices"]["high_temp"] == 2
        assert data["alerts"]["total"] == 4
        assert data["host_resources"][0]["ip"] == "192.0.2.100"

    def test_dashboard_summary_unknown_disk_avg_is_null(
        self, client, auth_headers, db_session, sample_host,
    ):
        """全员磁盘未知时 avg_disk_usage 为 null，不得回退成 0.0。"""
        sample_host.status = HostStatus.ONLINE.value
        sample_host.last_heartbeat = datetime.now(timezone.utc)
        sample_host.extra = {
            "cpu_load": 12.5,
            "ram_usage": 48.0,
            "disk_usage": {"usage_percent": None},
        }
        db_session.commit()

        response = client.get("/api/v1/stats/dashboard-summary", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()

        assert data["hosts"]["total"] == 1
        assert data["hosts"]["avg_cpu_load"] == 12.5
        assert data["hosts"]["avg_ram_usage"] == 48.0
        assert data["hosts"]["avg_disk_usage"] is None
        assert data["host_resources"][0]["disk_usage"] is None

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


def _make_plan_run(db_session, plan_id: str | int, *, status: str = "SUCCESS") -> "PlanRun":
    plan_run = PlanRun(
        plan_id=plan_id,
        status=status,
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
        plan = Plan(name="host-failure-plan", description="")
        db_session.add(plan)
        db_session.flush()
        plan_run = _make_plan_run(db_session, plan.id)

        devices = [
            sample_device,
            _make_device(db_session, sample_host.id, "HOST-RATE-2"),
            _make_device(db_session, sample_host.id, "HOST-RATE-3"),
        ]
        for status, device in zip(
            ("COMPLETED", "FAILED", "FAILED"), devices, strict=True,
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
        plan = Plan(name="host-failure-limit-plan", description="")
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


class TestPlanFailedDevices:
    """ADR-0048：排行按失败设备台数（事实计数），不再是成功率。"""

    def test_empty(self, client, auth_headers):
        response = client.get("/api/v1/stats/plan-failed-devices", headers=auth_headers)
        assert response.status_code == 200
        assert response.json() == {"items": [], "days": 30}

    def test_ranks_by_failed_devices_desc(
        self, client, auth_headers, db_session, sample_host, sample_device,
    ):
        now = datetime.now(timezone.utc)
        good_plan = Plan(name="good-plan", description="")
        bad_plan = Plan(name="bad-plan", description="")
        db_session.add_all([good_plan, bad_plan])
        db_session.flush()
        good_run = _make_plan_run(db_session, good_plan.id)
        bad_run = _make_plan_run(db_session, bad_plan.id, status="FAILED")
        second_device = _make_device(
            db_session, sample_host.id, "PLAN-RATE-DEVICE-2",
        )
        devices = [sample_device, second_device]

        # good_plan: 0 台失败; bad_plan: 2 台失败
        for status, device in zip(("COMPLETED", "COMPLETED"), devices, strict=True):
            db_session.add(JobInstance(
                plan_run_id=good_run.id, plan_id=good_plan.id,
                device_id=device.id, host_id=sample_host.id,
                status=status, pipeline_def={"lifecycle": {"init": [], "teardown": []}},
                started_at=now - timedelta(minutes=10), ended_at=now - timedelta(minutes=9),
            ))
        for status, device in zip(("FAILED", "FAILED"), devices, strict=True):
            db_session.add(JobInstance(
                plan_run_id=bad_run.id, plan_id=bad_plan.id,
                device_id=device.id, host_id=sample_host.id,
                status=status, pipeline_def={"lifecycle": {"init": [], "teardown": []}},
                started_at=now - timedelta(minutes=10), ended_at=now - timedelta(minutes=9),
            ))
        db_session.commit()

        response = client.get("/api/v1/stats/plan-failed-devices", headers=auth_headers)
        assert response.status_code == 200
        items = response.json()["items"]
        # #2848 改了这里的契约（原断言要求 good-plan 以 failed=0 出现在第 2 行）：
        # 这张图排的是失败台数，零失败行没有信息量——健康期它会让图里多出一排零高柱，
        # 而不是让空态出现。故 `HAVING` 从「滤空组」改成「滤零失败」。
        assert [(i["plan_name"], i["failed"]) for i in items] == [("bad-plan", 2)]


    def test_healthy_window_returns_no_rows_so_empty_state_shows(
        self, client, auth_headers, db_session, sample_host, sample_device,
    ) -> None:
        """全通过窗口 → items 为空（前端 `data.length === 0` 才拿得到空态，#2848）。"""
        now = datetime.now(timezone.utc)
        plan = Plan(name="all-ok-plan", description="")
        db_session.add(plan)
        db_session.flush()
        run = _make_plan_run(db_session, plan.id)
        db_session.add(JobInstance(
            plan_run_id=run.id, plan_id=plan.id,
            device_id=sample_device.id, host_id=sample_host.id,
            status="COMPLETED", pipeline_def={"lifecycle": {"init": [], "teardown": []}},
            started_at=now - timedelta(minutes=10), ended_at=now - timedelta(minutes=9),
        ))
        db_session.commit()

        items = client.get("/api/v1/stats/plan-failed-devices", headers=auth_headers).json()["items"]
        assert items == []

    def test_tie_break_is_servers_alone(
        self, client, auth_headers, db_session, sample_host, sample_device,
    ) -> None:
        """同失败数时按 `total_jobs DESC` 排——**这个 tie-break 只有服务端一处**（#2848）。

        前端原先二次 `sort(failed DESC)`，键比服务端少一个：同分次序两边可以各说一套。
        这里钉住服务端权威次序，前端用例钉住「不再重排」。
        """
        now = datetime.now(timezone.utc)
        few = Plan(name="tie-few", description="")
        many = Plan(name="tie-many", description="")
        db_session.add_all([few, many])
        db_session.flush()
        second_device = _make_device(db_session, sample_host.id, "TIE-DEVICE-2")
        for plan, runs in ((few, 1), (many, 3)):
            for _ in range(runs):
                run = _make_plan_run(db_session, plan.id, status="FAILED")
                db_session.add(JobInstance(
                    plan_run_id=run.id, plan_id=plan.id,
                    device_id=sample_device.id, host_id=sample_host.id,
                    status="FAILED", pipeline_def={"lifecycle": {"init": [], "teardown": []}},
                    started_at=now - timedelta(minutes=10), ended_at=now - timedelta(minutes=9),
                ))
            db_session.add(JobInstance(
                plan_run_id=_make_plan_run(db_session, plan.id).id, plan_id=plan.id,
                device_id=second_device.id, host_id=sample_host.id,
                status="COMPLETED", pipeline_def={"lifecycle": {"init": [], "teardown": []}},
                started_at=now - timedelta(minutes=10), ended_at=now - timedelta(minutes=9),
            ))
        db_session.commit()

        items = client.get("/api/v1/stats/plan-failed-devices", headers=auth_headers).json()["items"]
        assert [i["plan_name"] for i in items] == ["tie-many", "tie-few"], (
            "同 failed 数应按 total_jobs DESC 排（服务端唯一权威）"
        )


class TestPlanRunFailedDeviceTrend:
    """ADR-0048：日趋势统计失败设备台数事实，不再是平均通过率。"""

    def test_empty(self, client, auth_headers):
        response = client.get("/api/v1/stats/plan-run-failed-device-trend", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert "points" in data
        assert data["days"] == 30
        # 空数据也应返回 [since, today] 区间内每天一个占位点
        assert len(data["points"]) >= 1
        assert all(p["run_count"] == 0 for p in data["points"])

    def test_custom_days_param(self, client, auth_headers):
        response = client.get(
            "/api/v1/stats/plan-run-failed-device-trend", params={"days": 7}, headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["days"] == 7

    def test_aggregates_completed_plan_runs_by_day(
        self, client, auth_headers, db_session, sample_host, sample_device,
    ):
        now = datetime.now(timezone.utc)
        plan = Plan(name="trend-plan", description="")
        db_session.add(plan)
        db_session.flush()

        plan_run = PlanRun(
            plan_id=plan.id,
            status="SUCCESS",
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
            strict=True,
        ):
            db_session.add(JobInstance(
                plan_run_id=plan_run.id, plan_id=plan.id,
                device_id=device.id, host_id=sample_host.id,
                status=status, pipeline_def={"lifecycle": {"init": [], "teardown": []}},
                started_at=now - timedelta(minutes=10), ended_at=now - timedelta(minutes=9),
            ))
        db_session.commit()

        response = client.get("/api/v1/stats/plan-run-failed-device-trend", headers=auth_headers)
        assert response.status_code == 200
        points = response.json()["points"]
        today_point = next(p for p in points if p["date"] == now.date().isoformat())
        assert today_point["run_count"] == 1
        # 2 台里 1 台 FAILED → 当日失败设备数 = 1（事实计数，不再折算比率）
        assert today_point["failed_devices"] == 1


class TestFileServerOverview:
    """Endpoint /api/v1/stats/file-server — admin-only，未设共享根返回 503。"""

    def test_requires_admin(self, client, auth_headers):
        """非 admin 用户访问返回 403（与 dashboard-summary 等普通用户可用的端点区分）。"""
        response = client.get("/api/v1/stats/file-server", headers=auth_headers)
        assert response.status_code == 403

    def test_returns_503_when_shared_root_unset(
        self, client, admin_headers, monkeypatch,
    ):
        """STP_AEE_NFS_ROOT 未设时 endpoint 返回 503（非 500，非假数据）。

        防止 file_server_monitor 盯错路径永远生成 STORAGE_NOT_MOUNTED 误报（评审 #4）。
        patch stats 模块的本地引用（stats.py 走 `from ... import`，须改 stats 侧属性）。
        """
        from backend.api.routes import stats as stats_module

        def _raise_root_not_set(_hosts, *, hours=6):
            raise RuntimeError("STP_AEE_NFS_ROOT is not set")

        monkeypatch.setattr(
            stats_module, "collect_file_server_overview", _raise_root_not_set,
        )
        response = client.get("/api/v1/stats/file-server", headers=admin_headers)
        assert response.status_code == 503
        assert "STP_AEE_NFS_ROOT" in response.json()["detail"]

    def test_returns_overview_for_admin(
        self, client, admin_headers, monkeypatch,
    ):
        """admin 用户、共享根已配 → 200，schema 校验通过。"""
        from backend.api.routes import stats as stats_module

        overview = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "status": "healthy",
            "control_plane": {
                "node": {"hostname": "h", "address": "1.2.3.4", "cpu_count": 4, "uptime_seconds": 100.0},
                "system": {
                    "cpu_usage_pct": 5.0, "memory_usage_pct": 40.0, "memory_total_bytes": 1024,
                    "load1": 0.5,
                    "disk_read_bytes_per_second": None,
                    "disk_write_bytes_per_second": None,
                    "network_receive_bytes_per_second": None,
                    "network_transmit_bytes_per_second": None,
                },
                "client_mount": {
                    "path": "/mnt/nfs/aee_events", "source": "/dev/sda1", "filesystem": "ext4",
                    "mounted": True, "backend_write_access": True,
                },
                "monitoring": {"prometheus_available": True, "error": None},
                "processes": {
                    "available": True,
                    "error": None,
                    "items": [{"comm": "node", "unit": "app-gnome-x-1.scope", "anon_bytes": 123}],
                },
            },
            "storage_server": {
                "node": {"hostname": "h", "address": "1.2.3.4", "cpu_count": 4, "uptime_seconds": 100.0},
                "same_source": True,
                "system": {
                    "cpu_usage_pct": 5.0, "memory_usage_pct": 40.0, "memory_total_bytes": 1024,
                    "load1": 0.5,
                    "disk_read_bytes_per_second": None,
                    "disk_write_bytes_per_second": None,
                    "network_receive_bytes_per_second": None,
                    "network_transmit_bytes_per_second": None,
                },
                "disk": {
                    "path": "/mnt/nfs/aee_events", "source": "/dev/sda1", "filesystem": "ext4",
                    "mounted": True, "backend_write_access": True,
                    "total_bytes": 100, "used_bytes": 10, "available_bytes": 90, "used_pct": 10.0,
                    "inode_total": 200, "inode_used": 20, "inode_available": 180, "inode_used_pct": 10.0,
                },
                "nfs": {
                    "service_ready": True, "exported": True, "export_targets": ["10.0.0.0/8"],
                    "server_threads": 16, "requests_per_second": 1.5,
                    "rpc_errors_per_second": 0.0, "stale_file_handles_total": 0, "connections_total": 5,
                },
                "monitoring": {"prometheus_available": True, "error": None},
            },
            "agents": {
                "total": 1, "mounted": 1, "failed": 0, "unreported": 0,
                "items": [{
                    "host_id": "h1", "ip": "10.0.0.1", "status": "ONLINE",
                    "mounted": True, "last_heartbeat": "2026-07-28T00:00:00+00:00",
                }],
            },
            "device_log_disks": {
                "total": 1, "reported": 1, "warning": 0, "critical": 0,
                "items": [{
                    "host_id": "h1", "ip": "10.0.0.1", "path": "/mnt/hdd/aee_events",
                    "total_bytes": 1000, "used_bytes": 100, "available_bytes": 900,
                    "usage_percent": 10.0, "last_heartbeat": "2026-07-28T00:00:00+00:00",
                }],
            },
            "history": {
                "hours": 6, "capacity_usage_pct": [], "cpu_usage_pct": [],
                "memory_usage_pct": [], "nfs_requests_per_second": [],
                "hostproc_total_anon_bytes": [],
            },
            "alerts": [],
        }
        received_hours: list[int] = []
        monkeypatch.setattr(
            stats_module,
            "collect_file_server_overview",
            lambda _hosts, *, hours=6: received_hours.append(hours) or overview,
        )
        response = client.get("/api/v1/stats/file-server", headers=admin_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["storage_server"]["disk"]["path"] == "/mnt/nfs/aee_events"
        assert data["storage_server"]["same_source"] is True
        assert data["agents"]["mounted"] == 1
        assert data["device_log_disks"]["reported"] == 1
        # 进程内存面板随 control_plane 一起过 schema 校验
        assert data["control_plane"]["processes"]["items"][0]["comm"] == "node"
        assert received_hours == [6]

        # 7 天历史（168h）在合法范围内；169h 超出上限被参数校验拒绝。
        response = client.get(
            "/api/v1/stats/file-server", params={"hours": 168}, headers=admin_headers,
        )
        assert response.status_code == 200
        assert received_hours[-1] == 168
        response = client.get(
            "/api/v1/stats/file-server", params={"hours": 169}, headers=admin_headers,
        )
        assert response.status_code == 422
