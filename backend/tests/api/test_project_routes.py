"""ADR-0029 P2 — 项目登记簿 API + 列表 project_key 筛选 + 设备批量归入。

覆盖：
- GET /api/v1/projects（列表 + 设备数 / 在跑 Run 数聚合）
- GET /api/v1/projects/{project_key}（详情计数 + 最近 Run；404）
- POST /api/v1/devices/bulk-project（admin 归属变更 + audit 留痕 +
  幂等跳过 + 未知 key/device 拒绝 + 非 admin 403）
- devices / plans / plan-runs / results 列表接口的 project_key 筛选

口径（F2）：对外一律 project_key；project_id NULL 的设备不命中筛选。
"""
from __future__ import annotations

import pytest

from backend.models.audit import AuditLog
from backend.models.host import Device
from backend.models.job import JobInstance
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun
from backend.models.test_project import TestProject as ProjectModel


@pytest.fixture
def project_a(db_session):
    p = ProjectModel(
        project_key="proj-a",
        display_name="Project A",
        customer="CustA",
        platform="MTK",
        form_factor="PHONE",
    )
    db_session.add(p)
    db_session.commit()
    return p


@pytest.fixture
def project_legacy(db_session):
    p = ProjectModel(
        project_key="LEGACY",
        display_name="Legacy",
        customer=None,
        platform=None,
        form_factor=None,
    )
    db_session.add(p)
    db_session.commit()
    return p


def _make_device(db_session, serial: str, project: ProjectModel | None = None) -> Device:
    device = Device(serial=serial, project_id=project.id if project else None)
    db_session.add(device)
    db_session.commit()
    return device


def _make_plan(db_session, name: str, project: ProjectModel | None = None) -> Plan:
    plan = Plan(name=name, project_id=project.id if project else None)
    db_session.add(plan)
    db_session.commit()
    return plan


def _make_run(
    db_session,
    plan: Plan,
    status: str = "SUCCESS",
    project: ProjectModel | None = None,
) -> PlanRun:
    run = PlanRun(
        plan_id=plan.id,
        project_id=project.id if project else None,
        plan_snapshot={},
        run_type="MANUAL",
        status=status,
    )
    db_session.add(run)
    db_session.commit()
    return run


class TestListProjects:
    def test_empty_returns_ok(self, client, auth_headers):
        resp = client.get("/api/v1/projects", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["data"] == []

    def test_aggregates_device_and_running_run_counts(
        self, client, auth_headers, db_session, project_a, project_legacy
    ):
        _make_device(db_session, "s-agg-1", project_a)
        _make_device(db_session, "s-agg-2", project_a)
        _make_device(db_session, "s-agg-3", None)  # NULL 不属任何项目
        plan = _make_plan(db_session, "plan-agg", project_a)
        _make_run(db_session, plan, status="RUNNING", project=project_a)
        _make_run(db_session, plan, status="SUCCESS", project=project_a)

        resp = client.get("/api/v1/projects", headers=auth_headers)
        assert resp.status_code == 200
        by_key = {p["project_key"]: p for p in resp.json()["data"]}
        assert by_key["proj-a"]["device_count"] == 2
        assert by_key["proj-a"]["running_run_count"] == 1  # 只计 RUNNING，不计 SUCCESS
        assert by_key["LEGACY"]["device_count"] == 0
        # 无项目字段泄露数字 id（F2）
        assert "id" not in by_key["proj-a"]


class TestGetProject:
    def test_detail_counts_and_recent_runs(
        self, client, auth_headers, db_session, project_a
    ):
        plan = _make_plan(db_session, "plan-detail", project_a)
        _make_run(db_session, plan, status="SUCCESS", project=project_a)
        _make_run(db_session, plan, status="FAILED", project=project_a)
        _make_device(db_session, "s-detail-1", project_a)

        resp = client.get("/api/v1/projects/proj-a", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["device_count"] == 1
        assert data["plan_count"] == 1
        assert data["total_run_count"] == 2
        statuses = {r["status"] for r in data["recent_runs"]}
        assert statuses == {"SUCCESS", "FAILED"}

    def test_unknown_key_404(self, client, auth_headers):
        resp = client.get("/api/v1/projects/no-such-project", headers=auth_headers)
        assert resp.status_code == 404


class TestBulkAssignProject:
    def test_assign_updates_devices_and_records_audit(
        self, client, db_session, project_a, project_legacy, admin_headers
    ):
        d1 = _make_device(db_session, "s-bulk-1", None)
        d2 = _make_device(db_session, "s-bulk-2", project_legacy)

        resp = client.post(
            "/api/v1/devices/bulk-project",
            headers=admin_headers,
            json={"project_key": "proj-a", "device_ids": [d1.id, d2.id]},
        )
        assert resp.status_code == 200
        returned = {d["serial"]: d for d in resp.json()["data"]}
        assert returned["s-bulk-1"]["project_key"] == "proj-a"
        assert returned["s-bulk-2"]["project_key"] == "proj-a"

        # DB 归属更新
        db_session.refresh(d1)
        db_session.refresh(d2)
        assert d1.project_id == project_a.id
        assert d2.project_id == project_a.id

        # audit 留痕（D2：每台实际变更一条，details 含 from/to key）
        audits = (
            db_session.query(AuditLog)
            .filter(AuditLog.action == "assign_project", AuditLog.resource_type == "device")
            .order_by(AuditLog.id)
            .all()
        )
        assert len(audits) == 2
        details = {a.resource_id: a.details for a in audits}
        assert details[str(d1.id)] == {"project_key": "proj-a", "from_project_key": None}
        assert details[str(d2.id)] == {
            "project_key": "proj-a",
            "from_project_key": "LEGACY",
        }

    def test_idempotent_skip_no_audit(
        self, client, db_session, project_a, admin_headers
    ):
        d1 = _make_device(db_session, "s-bulk-idem", project_a)
        resp = client.post(
            "/api/v1/devices/bulk-project",
            headers=admin_headers,
            json={"project_key": "proj-a", "device_ids": [d1.id]},
        )
        assert resp.status_code == 200
        audits = (
            db_session.query(AuditLog)
            .filter(AuditLog.action == "assign_project")
            .all()
        )
        assert audits == []

    def test_unknown_project_404(self, client, db_session, admin_headers):
        _make_device(db_session, "s-bulk-404")
        resp = client.post(
            "/api/v1/devices/bulk-project",
            headers=admin_headers,
            json={"project_key": "nope", "device_ids": [1]},
        )
        assert resp.status_code == 404

    def test_unknown_device_404_rejects_whole_batch(
        self, client, db_session, project_a, admin_headers
    ):
        d1 = _make_device(db_session, "s-bulk-partial")
        resp = client.post(
            "/api/v1/devices/bulk-project",
            headers=admin_headers,
            json={"project_key": "proj-a", "device_ids": [d1.id, 999999]},
        )
        assert resp.status_code == 404
        db_session.refresh(d1)
        assert d1.project_id is None  # 整体拒绝，防部分成功

    def test_empty_device_ids_422(self, client, project_a, admin_headers):
        resp = client.post(
            "/api/v1/devices/bulk-project",
            headers=admin_headers,
            json={"project_key": "proj-a", "device_ids": []},
        )
        assert resp.status_code == 422

    def test_forbidden_for_non_admin(self, client, db_session, project_a, auth_headers):
        _make_device(db_session, "s-bulk-user")
        resp = client.post(
            "/api/v1/devices/bulk-project",
            headers=auth_headers,
            json={"project_key": "proj-a", "device_ids": [1]},
        )
        assert resp.status_code == 403


class TestListFilters:
    def test_devices_filter_by_project_key(
        self, client, auth_headers, db_session, project_a, project_legacy
    ):
        _make_device(db_session, "s-filt-a", project_a)
        _make_device(db_session, "s-filt-legacy", project_legacy)
        _make_device(db_session, "s-filt-null", None)

        resp = client.get(
            "/api/v1/devices?project_key=proj-a", headers=auth_headers
        )
        assert resp.status_code == 200
        serials = {d["serial"] for d in resp.json()}
        assert serials == {"s-filt-a"}  # NULL 与 LEGACY 都不命中
        assert resp.json()[0]["project_key"] == "proj-a"

    def test_plans_filter_by_project_key(
        self, client, auth_headers, db_session, project_a
    ):
        _make_plan(db_session, "plan-filt-a", project_a)
        _make_plan(db_session, "plan-filt-null", None)

        resp = client.get("/api/v1/plans?project_key=proj-a", headers=auth_headers)
        assert resp.status_code == 200
        names = {p["name"] for p in resp.json()["data"]}
        assert names == {"plan-filt-a"}

    def test_plan_runs_filter_by_project_key(
        self, client, auth_headers, db_session, project_a
    ):
        plan_a = _make_plan(db_session, "plan-run-filt-a", project_a)
        plan_null = _make_plan(db_session, "plan-run-filt-null", None)
        _make_run(db_session, plan_a, project=project_a)
        _make_run(db_session, plan_null, project=None)

        resp = client.get(
            "/api/v1/plan-runs?project_key=proj-a", headers=auth_headers
        )
        assert resp.status_code == 200
        run_ids = [r["id"] for r in resp.json()["data"]]
        assert len(run_ids) == 1

    def test_results_summary_filter_by_project_key(
        self, client, auth_headers, db_session, project_a, sample_host
    ):
        from backend.models.host import Device

        plan_a = _make_plan(db_session, "summary-plan-a", project_a)
        plan_null = _make_plan(db_session, "summary-plan-null", None)
        run_a = _make_run(db_session, plan_a, status="RUNNING", project=project_a)
        run_null = _make_run(db_session, plan_null, status="RUNNING", project=None)
        device_a = Device(serial="s-summary-a", host_id=sample_host.id)
        device_null = Device(serial="s-summary-null", host_id=sample_host.id)
        db_session.add_all([device_a, device_null])
        db_session.commit()
        db_session.add_all([
            JobInstance(
                plan_run_id=run_a.id, plan_id=plan_a.id,
                device_id=device_a.id, pipeline_def={}, status="RUNNING",
            ),
            JobInstance(
                plan_run_id=run_null.id, plan_id=plan_null.id,
                device_id=device_null.id, pipeline_def={}, status="RUNNING",
            ),
        ])
        db_session.commit()

        resp = client.get(
            "/api/v1/results/summary?project_key=proj-a", headers=auth_headers
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["runs_by_status"]["total"] == 1
        assert body["runs_by_status"]["running"] == 1
        assert len(body["recent_runs"]) == 1

    def test_results_summary_uses_plan_run_snapshot_not_plan_ownership(
        self, client, auth_headers, db_session, project_a, project_legacy, sample_host
    ):
        """D5 快照语义：Plan 改归属后，历史 Run 仍按 plan_run.project_id 过滤。

        修复前按 Plan.project_id 过滤——Plan 挪到 LEGACY 后该 Run 会追溯性
        改归属（旧实现此测试断言会翻转）。
        """
        from backend.models.host import Device

        plan = _make_plan(db_session, "plan-snapshot", project_a)
        run = _make_run(db_session, plan, status="SUCCESS", project=project_a)
        # 变更 Plan 的当前归属（P2 上线后的真实操作）
        plan.project_id = project_legacy.id
        db_session.commit()
        device = Device(serial="s-snapshot", host_id=sample_host.id)
        db_session.add(device)
        db_session.commit()
        db_session.add(JobInstance(
            plan_run_id=run.id, plan_id=plan.id, device_id=device.id,
            pipeline_def={}, status="COMPLETED",
        ))
        db_session.commit()

        # Run 快照仍在 proj-a → 该 key 下可见
        resp_a = client.get(
            "/api/v1/results/summary?project_key=proj-a", headers=auth_headers
        )
        assert resp_a.status_code == 200
        assert resp_a.json()["runs_by_status"]["total"] == 1

        # Plan 已挪走但 Run 快照不跟着变 → LEGACY 下为零
        resp_legacy = client.get(
            "/api/v1/results/summary?project_key=LEGACY", headers=auth_headers
        )
        assert resp_legacy.status_code == 200
        assert resp_legacy.json()["runs_by_status"]["total"] == 0

    def test_unknown_project_key_404_across_list_endpoints(
        self, client, auth_headers
    ):
        """未知 key 四端点语义统一：404（key 是路径段，拼错即路由错误）。"""
        urls = (
            "/api/v1/devices?project_key=nope",
            "/api/v1/plans?project_key=nope",
            "/api/v1/plan-runs?project_key=nope",
            "/api/v1/results/summary?project_key=nope",
        )
        for url in urls:
            resp = client.get(url, headers=auth_headers)
            assert resp.status_code == 404, url
