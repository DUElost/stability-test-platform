"""ADR-0029 P2 / P2.5 — 项目登记簿 API + Fleet 事实 + 人工映射。

覆盖：
- GET /api/v1/projects（默认只返回 source=USER；SEED 回填标签不出现）
- POST /api/v1/projects（admin 新建 USER 项目）
- GET /api/v1/projects/inventory/models（fleet 按 model；mapped 只含 USER）
- GET /api/v1/projects/inventory/summary
- POST /api/v1/projects/{key}/map/preview|apply
- GET /api/v1/projects/{project_key}/models
- GET /api/v1/projects/{project_key}
- POST /api/v1/devices/bulk-project
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
from backend.models.project import Customer, TestProject
from backend.models.project_model import ProjectModel


@pytest.fixture
def project_a(db_session):
    p = TestProject(
        project_key="proj-a",
        display_name="Project A",
        customer="CustA",
        source="USER",
    )
    db_session.add(p)
    db_session.commit()
    return p


@pytest.fixture
def project_legacy(db_session):
    p = TestProject(
        project_key="LEGACY",
        display_name="Legacy",
        customer=None,
        source="SEED",
    )
    db_session.add(p)
    db_session.commit()
    return p


def _make_device(
    db_session,
    serial: str,
    project: TestProject | None = None,
    *,
    model: str | None = None,
    platform: str | None = None,
) -> Device:
    # ADR-0029 v2.5 D10：归属派生——设备归属 = 其型号的活跃成员行，
    # 不再写 device.project_id 副本（M3 删列）。
    device = Device(
        serial=serial,
        model=model,
        platform=platform,
    )
    db_session.add(device)
    db_session.commit()
    if project is not None:
        if not model:
            raise ValueError("派生归属需要 device.model 才能建成员行")
        # 型号级归属：一个型号全局只有一个活跃成员行（uq_project_model_active）
        # ——已映射的型号不再建行，设备继承既有映射。
        existing = db_session.query(ProjectModel).filter(
            ProjectModel.match_value == model,
            ProjectModel.is_active.is_(True),
        ).first()
        if existing is None:
            db_session.add(ProjectModel(
                project_id=project.id, match_value=model, is_active=True,
            ))
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
        # v2.5 M4：GENERIC 哨兵已删
        assert resp.json()["data"] == []

    def test_aggregates_device_and_running_run_counts(
        self, client, auth_headers, db_session, project_a, project_legacy
    ):
        from backend.models.project_model import ProjectModel

        db_session.add(ProjectModel(
            project_id=project_a.id, match_value="AGG_MODEL"))
        db_session.commit()
        _make_device(db_session, "s-agg-1", project_a, model="AGG_MODEL")
        _make_device(db_session, "s-agg-2", project_a, model="AGG_MODEL")
        _make_device(db_session, "s-agg-3", None, model="NO_MODEL")
        plan = _make_plan(db_session, "plan-agg", project_a)
        _make_run(db_session, plan, status="RUNNING", project=project_a)
        _make_run(db_session, plan, status="SUCCESS", project=project_a)

        resp = client.get("/api/v1/projects", headers=auth_headers)
        assert resp.status_code == 200
        by_key = {p["project_key"]: p for p in resp.json()["data"]}
        assert by_key["proj-a"]["device_count"] == 2
        assert by_key["proj-a"]["running_run_count"] == 1  # 只计 RUNNING，不计 SUCCESS
        assert by_key["proj-a"]["source"] == "USER"
        assert "LEGACY" not in by_key
        # 无项目字段泄露数字 id（F2）
        assert "id" not in by_key["proj-a"]

        all_resp = client.get("/api/v1/projects?source=all", headers=auth_headers)
        assert all_resp.status_code == 200
        all_keys = {p["project_key"] for p in all_resp.json()["data"]}
        assert all_keys == {"proj-a", "LEGACY"}

    def test_platforms_derived_from_devices(
        self, client, auth_headers, db_session, project_a
    ):
        """ADR-0029 P1-B：项目平台从设备派生（distinct，UNKNOWN 不展示）。"""
        from backend.models.project_model import ProjectModel

        db_session.add_all([
            ProjectModel(project_id=project_a.id, match_value="MLD_LX2"),
            ProjectModel(project_id=project_a.id, match_value="MLD_LX3"),
            ProjectModel(project_id=project_a.id, match_value="MLD_LX4"),
        ])
        db_session.commit()
        _make_device(db_session, "s-p1", project_a, model="MLD_LX2", platform="MTK")
        _make_device(db_session, "s-p2", project_a, model="MLD_LX3", platform="UNISOC")
        _make_device(db_session, "s-p3", project_a, model="MLD_LX2", platform="MTK")
        _make_device(db_session, "s-p4", project_a, model="MLD_LX4", platform="UNKNOWN")

        resp = client.get("/api/v1/projects", headers=auth_headers)
        by_key = {p["project_key"]: p for p in resp.json()["data"]}
        assert by_key["proj-a"]["platforms"] == ["MTK", "UNISOC"]
        assert "platform" not in by_key["proj-a"]

    def test_status_filter_archived(self, client, auth_headers, db_session, project_a):
        """ADR-0029 P0：status 过滤——归档项目可从列表筛出（修「归档是 no-op」）。"""
        project_a.status = "ARCHIVED"
        db_session.commit()

        active = client.get("/api/v1/projects?status=ACTIVE", headers=auth_headers)
        assert active.status_code == 200
        assert active.json()["data"] == []

        archived = client.get("/api/v1/projects?status=ARCHIVED", headers=auth_headers)
        assert archived.status_code == 200
        keys = {p["project_key"] for p in archived.json()["data"]}
        assert keys == {"proj-a"}

        # 缺省不带 status = 全量（既有行为不变）
        all_projects = client.get("/api/v1/projects", headers=auth_headers)
        keys_all = {p["project_key"] for p in all_projects.json()["data"]}
        assert keys_all == {"proj-a"}


class TestGetProject:
    def test_detail_counts_and_recent_runs(
        self, client, auth_headers, db_session, project_a
    ):
        from backend.models.project_model import ProjectModel

        db_session.add(ProjectModel(
            project_id=project_a.id, match_value="DETAIL_MODEL"))
        db_session.commit()
        plan = _make_plan(db_session, "plan-detail", project_a)
        _make_run(db_session, plan, status="SUCCESS", project=project_a)
        _make_run(db_session, plan, status="FAILED", project=project_a)
        _make_device(db_session, "s-detail-1", project_a, model="DETAIL_MODEL")

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


class TestInventoryModels:
    """P2.5：Fleet 事实层。SEED 回填不算已映射项目。"""

    def test_inventory_path_not_captured_as_project_key(self, client, auth_headers):
        resp = client.get("/api/v1/projects/inventory/models", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["data"] == []

    def test_empty_fleet(self, client, auth_headers):
        resp = client.get("/api/v1/projects/inventory/summary", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["data"] == {
            "total_devices": 0,
            "user_mapped_devices": 0,
            "distinct_models": 0,
            "unmapped_models": [],
            "unassigned_devices": 0,
        }

    def test_groups_by_model_seed_is_not_mapping(
        self, client, auth_headers, db_session, project_a, project_legacy
    ):
        from backend.models.project_model import ProjectModel

        db_session.add(ProjectModel(
            project_id=project_a.id, match_value="MLD_LX2"))
        db_session.commit()
        _make_device(
            db_session, "s-mld-1", project_a, model="MLD_LX2", platform="MTK"
        )
        _make_device(
            db_session, "s-mld-2", project_a, model="MLD_LX2", platform="MTK"
        )
        _make_device(
            db_session, "s-mld-3", None, model="MLD_LX3", platform="MTK"
        )
        _make_device(
            db_session, "s-legacy", project_legacy, model="MYSTERY_X", platform="MTK"
        )
        _make_device(
            db_session, "s-null", None, model="MYSTERY_X", platform="MTK"
        )
        _make_device(db_session, "s-blank-none", None, model=None, platform=None)
        _make_device(db_session, "s-blank-empty", None, model="  ", platform="  ")

        resp = client.get("/api/v1/projects/inventory/models", headers=auth_headers)
        assert resp.status_code == 200
        by_model = {row["model"]: row for row in resp.json()["data"]}

        assert by_model["MLD_LX2"]["device_count"] == 2
        assert by_model["MLD_LX2"]["platforms"] == ["MTK"]
        assert by_model["MLD_LX2"]["mapped_project_keys"] == ["proj-a"]
        assert by_model["MLD_LX2"]["unassigned_device_count"] == 0
        assert "backfill_project_keys" not in by_model["MLD_LX2"]
        assert "project_keys" not in by_model["MLD_LX2"]

        assert by_model["MLD_LX3"]["device_count"] == 1
        assert by_model["MLD_LX3"]["mapped_project_keys"] == []
        assert by_model["MLD_LX3"]["unassigned_device_count"] == 1
        assert by_model["MYSTERY_X"]["mapped_project_keys"] == []
        assert by_model["MYSTERY_X"]["unassigned_device_count"] == 2
        assert by_model[None]["device_count"] == 2
        assert by_model[None]["unassigned_device_count"] == 2

        models_in_order = [row["model"] for row in resp.json()["data"]]
        assert models_in_order == [None, "MLD_LX2", "MYSTERY_X", "MLD_LX3"]

        summary = client.get(
            "/api/v1/projects/inventory/summary", headers=auth_headers
        ).json()["data"]
        assert summary["total_devices"] == 7
        # v2.5 型号级口径：仅 MLD_LX2（成员行）的 2 台 mapped
        assert summary["user_mapped_devices"] == 2
        assert summary["distinct_models"] == 4
        assert set(summary["unmapped_models"]) == {None, "MYSTERY_X", "MLD_LX3"}
        # 严格未映射口径：型号无成员行的设备数（MLD_LX3 1 + MYSTERY_X 2 +
        # None/空白 2）
        assert summary["unassigned_devices"] == 5

    def test_mixed_user_and_seed_on_same_model(
        self, client, auth_headers, db_session, project_a, project_legacy
    ):
        from backend.models.project_model import ProjectModel

        db_session.add(ProjectModel(
            project_id=project_a.id, match_value="MLD_LX2"))
        db_session.commit()
        _make_device(
            db_session, "s-mix-a", project_a, model="MLD_LX2", platform="MTK"
        )
        _make_device(
            db_session, "s-mix-legacy", project_legacy, model="MLD_LX2", platform="MTK"
        )
        resp = client.get("/api/v1/projects/inventory/models", headers=auth_headers)
        row = resp.json()["data"][0]
        assert row["model"] == "MLD_LX2"
        assert row["mapped_project_keys"] == ["proj-a"]
        # v2.5 型号级：成员行覆盖全部设备（含原 LEGACY 的），无未映射
        assert row["unassigned_device_count"] == 0
        assert row["device_count"] == 2


class TestProjectModelCoverage:
    def test_reverse_lookup_counts_only_this_label(
        self, client, auth_headers, db_session, project_a, project_legacy
    ):
        from backend.models.project_model import ProjectModel

        db_session.add_all([
            ProjectModel(project_id=project_a.id, match_value="MLD_LX2"),
            ProjectModel(project_id=project_a.id, match_value="MLD_LX3"),
        ])
        db_session.commit()
        _make_device(
            db_session, "s-cov-a1", project_a, model="MLD_LX2", platform="MTK"
        )
        _make_device(
            db_session, "s-cov-a2", project_a, model="MLD_LX3", platform="MTK"
        )
        _make_device(
            db_session, "s-cov-legacy", project_legacy, model="MLD_LX2", platform="MTK"
        )

        resp = client.get("/api/v1/projects/proj-a/models", headers=auth_headers)
        assert resp.status_code == 200
        by_model = {row["model"]: row for row in resp.json()["data"]}
        # v2.5 派生：成员行按型号覆盖全部设备（含原 LEGACY 的 s-cov-legacy）
        assert by_model["MLD_LX2"]["device_count"] == 2
        assert by_model["MLD_LX3"]["device_count"] == 1
        assert "mapped_project_keys" not in by_model["MLD_LX2"]

    def test_unknown_key_404(self, client, auth_headers):
        resp = client.get("/api/v1/projects/no-such-project/models", headers=auth_headers)
        assert resp.status_code == 404


class TestCreateProject:
    def test_admin_creates_user_project(self, client, admin_headers, db_session):
        resp = client.post(
            "/api/v1/projects",
            headers=admin_headers,
            json={"project_key": "HONOR-CAMERA", "display_name": " 荣耀相机 "},
        )
        assert resp.status_code == 201
        data = resp.json()["data"]
        assert data["project_key"] == "HONOR-CAMERA"
        assert data["display_name"] == "荣耀相机"
        assert data["source"] == "USER"
        assert data["match_models"] == []
        audits = (
            db_session.query(AuditLog)
            .filter(AuditLog.action == "create_project")
            .all()
        )
        assert len(audits) == 1

    def test_reserved_seed_key_422(self, client, admin_headers):
        resp = client.post(
            "/api/v1/projects",
            headers=admin_headers,
            json={"project_key": "HONOR-MLD", "display_name": "Nope"},
        )
        assert resp.status_code == 422

    def test_duplicate_409(self, client, admin_headers, project_a):
        resp = client.post(
            "/api/v1/projects",
            headers=admin_headers,
            json={"project_key": "proj-a", "display_name": "Dup"},
        )
        assert resp.status_code == 409

    def test_duplicate_case_variant_409(self, client, admin_headers, project_a):
        resp = client.post(
            "/api/v1/projects",
            headers=admin_headers,
            json={"project_key": "PROJ-A", "display_name": "Dup"},
        )
        assert resp.status_code == 409

    def test_forbidden_for_non_admin(self, client, auth_headers):
        resp = client.post(
            "/api/v1/projects",
            headers=auth_headers,
            json={"project_key": "NEW-PROJ", "display_name": "New"},
        )
        assert resp.status_code == 403


class TestUpdateAndArchiveProject:
    """#406 — PUT facet + archive；逐字段审计；SEED 不可改。"""

    def test_put_updates_facets_with_per_field_audit(
        self, client, admin_headers, db_session, project_a
    ):
        resp = client.put(
            "/api/v1/projects/proj-a",
            headers=admin_headers,
            json={
                "display_name": "Project A Renamed",
                "customer": "CustB",
                "jira_project_key": "CAM",
            },
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["display_name"] == "Project A Renamed"
        assert data["customer"] == "CustB"
        assert data["jira_project_key"] == "CAM"
        # P1-B：platform 删列改派生——未提交 facet 不动、platforms 空（无设备）
        assert "platform" not in data
        assert data["platforms"] == []

        audits = (
            db_session.query(AuditLog)
            .filter(AuditLog.action == "update_project")
            .all()
        )
        by_field = {a.details["field"]: a.details for a in audits}
        assert set(by_field) == {"display_name", "customer", "jira_project_key"}
        assert by_field["customer"]["old"] == "CustA"
        assert by_field["customer"]["new"] == "CustB"
        assert by_field["jira_project_key"]["old"] is None
        assert by_field["jira_project_key"]["new"] == "CAM"
        assert all(a.details["project_key"] == "proj-a" for a in audits)

    def test_put_clears_nullable_facet_with_null(
        self, client, admin_headers, db_session, project_a
    ):
        resp = client.put(
            "/api/v1/projects/proj-a",
            headers=admin_headers,
            json={"customer": None},
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["customer"] is None
        audit = (
            db_session.query(AuditLog)
            .filter(AuditLog.action == "update_project")
            .one()
        )
        assert audit.details["field"] == "customer"
        assert audit.details["old"] == "CustA"
        assert audit.details["new"] is None

    def test_put_empty_body_422(self, client, admin_headers, project_a):
        resp = client.put(
            "/api/v1/projects/proj-a",
            headers=admin_headers,
            json={},
        )
        assert resp.status_code == 422

    def test_put_seed_project_422(self, client, admin_headers, project_legacy):
        resp = client.put(
            "/api/v1/projects/LEGACY",
            headers=admin_headers,
            json={"display_name": "Nope"},
        )
        assert resp.status_code == 422

    def test_archive_sets_status_and_blocks_further_update(
        self, client, admin_headers, db_session, project_a
    ):
        resp = client.post(
            "/api/v1/projects/proj-a/archive",
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["status"] == "ARCHIVED"
        audits = (
            db_session.query(AuditLog)
            .filter(AuditLog.action == "archive_project")
            .all()
        )
        assert len(audits) == 1
        assert audits[0].details["to_status"] == "ARCHIVED"

        again = client.post(
            "/api/v1/projects/proj-a/archive",
            headers=admin_headers,
        )
        assert again.status_code == 409

        update = client.put(
            "/api/v1/projects/proj-a",
            headers=admin_headers,
            json={"display_name": "After Archive"},
        )
        assert update.status_code == 409

    def test_archive_seed_allowed(self, client, admin_headers, project_legacy):
        # #644 P2-7：SEED 可归档 = 显式放弃待转正标签（退场路径）
        resp = client.post(
            "/api/v1/projects/LEGACY/archive",
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["status"] == "ARCHIVED"
        # LEGACY 兜底标签的 promote 拒绝在 ARCHIVED 检查之前（422 先命中）
        promote = client.post(
            "/api/v1/projects/seed/LEGACY/promote",
            headers=admin_headers,
        )
        assert promote.status_code == 422

    def test_forbidden_for_non_admin(self, client, auth_headers, project_a):
        assert (
            client.put(
                "/api/v1/projects/proj-a",
                headers=auth_headers,
                json={"display_name": "X"},
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/api/v1/projects/proj-a/archive",
                headers=auth_headers,
            ).status_code
            == 403
        )


class TestMapProject:
    @staticmethod
    def _active_rules(db_session, project_id):
        from backend.models.project_model import ProjectModel

        return [
            r.match_value
            for r in db_session.query(ProjectModel)
            .filter_by(project_id=project_id, is_active=True)
            .order_by(ProjectModel.match_value)
            .all()
        ]

    def test_preview_and_apply_from_seed_and_null(
        self, client, admin_headers, db_session, project_a, project_legacy
    ):
        # 型号级归属（v2.5 D10）：三个型号分别覆盖 seed 归属 / 未映射 / 已在目标
        _make_device(db_session, "s-map-seed", project_legacy, model="SEED_M")
        _make_device(db_session, "s-map-null", None, model="NULL_M")
        _make_device(db_session, "s-map-already", project_a, model="ALREADY_M")

        preview = client.post(
            "/api/v1/projects/proj-a/map/preview",
            headers=admin_headers,
            json={"models": ["SEED_M", "NULL_M", "ALREADY_M"]},
        )
        assert preview.status_code == 200
        body = preview.json()["data"]
        assert body["will_assign"] == 2       # SEED_M（SEED 不冲突）+ NULL_M（未映射）
        assert body["already_in_target"] == 1  # ALREADY_M
        assert body["conflicts"] == []

        applied = client.post(
            "/api/v1/projects/proj-a/map/apply",
            headers=admin_headers,
            json={"models": ["SEED_M", "NULL_M", "ALREADY_M"]},
        )
        assert applied.status_code == 200
        # v2.5 M3：apply 只写成员行——归属派生自 device.model ⋈ 成员行
        assert self._active_rules(db_session, project_a.id) == ["ALREADY_M", "NULL_M", "SEED_M"]

        inventory = client.get(
            "/api/v1/projects/inventory/models", headers=admin_headers
        ).json()["data"]
        by_model = {row["model"]: row for row in inventory}
        assert by_model["ALREADY_M"]["mapped_project_keys"] == ["proj-a"]
        assert by_model["ALREADY_M"]["unassigned_device_count"] == 0

        audits = {
            a.action
            for a in db_session.query(AuditLog).filter(
                AuditLog.action.in_(("assign_project", "apply_project_model"))
            )
        }
        # v2.5 M3：apply 只写成员行——无逐设备 assign_project
        assert audits == {"apply_project_model"}

    def test_user_conflict_skipped_unless_reassign(
        self, client, admin_headers, db_session, project_a
    ):
        other = TestProject(
            project_key="proj-b",
            display_name="Project B",
            source="USER",
        )
        db_session.add(other)
        db_session.commit()
        _make_device(db_session, "s-conflict", other, model="MLD_LX2")
        _make_device(db_session, "s-free", None, model="MLD_LX2")

        preview = client.post(
            "/api/v1/projects/proj-a/map/preview",
            headers=admin_headers,
            json={"models": ["MLD_LX2"]},
        ).json()["data"]
        # 型号级冲突：MLD_LX2 已是 USER 项目 proj-b 的成员 → 整型设备都在冲突
        assert preview["will_assign"] == 0
        assert {c["serial"] for c in preview["conflicts"]} == {"s-conflict", "s-free"}
        # #644 P1：from_project_key 是占用方 key（proj-b），不是 source（'USER'）
        assert {c["from_project_key"] for c in preview["conflicts"]} == {"proj-b"}

        blocked = client.post(
            "/api/v1/projects/proj-a/map/apply",
            headers=admin_headers,
            json={"models": ["MLD_LX2"]},
        )
        assert blocked.status_code == 409
        assert self._active_rules(db_session, project_a.id) == []

        applied = client.post(
            "/api/v1/projects/proj-a/map/apply",
            headers=admin_headers,
            json={"models": ["MLD_LX2"], "reassign_conflicts": True},
        )
        assert applied.status_code == 200
        # v2.5：apply 只写成员行（reassign_conflicts 语义保留——冲突型号可覆盖）
        assert self._active_rules(db_session, project_a.id) == ["MLD_LX2"]

    def test_seed_project_cannot_be_mapped(
        self, client, admin_headers, project_legacy
    ):
        resp = client.post(
            "/api/v1/projects/LEGACY/map/preview",
            headers=admin_headers,
            json={"models": ["MLD_LX2"]},
        )
        assert resp.status_code == 422

    def test_unknown_project_404(self, client, admin_headers):
        resp = client.post(
            "/api/v1/projects/nope/map/apply",
            headers=admin_headers,
            json={"models": ["MLD_LX2"]},
        )
        assert resp.status_code == 404

    def test_forbidden_for_non_admin(self, client, auth_headers, project_a):
        resp = client.post(
            "/api/v1/projects/proj-a/map/apply",
            headers=auth_headers,
            json={"models": ["MLD_LX2"]},
        )
        assert resp.status_code == 403


class TestPromoteSeedProject:
    """ADR-0029 P0：SEED 回填标签转正为人工项目。"""

    @staticmethod
    def _seed_project(db_session, *, key="HONOR-ELA", status="ACTIVE"):

        p = TestProject(
            project_key=key,
            display_name="Honor ELA",
            customer="荣耀",
            source="SEED",
            status=status,
        )
        db_session.add(p)
        db_session.commit()
        return p

    def test_promote_creates_user_project_and_moves_devices(
        self, client, db_session, admin_headers
    ):
        seed = self._seed_project(db_session)
        _make_device(db_session, "s-ela-1", seed, model="MLD_LX2", platform="MTK")
        _make_device(db_session, "s-ela-2", seed, model="MLD_LX3", platform="MTK")
        db_session.commit()

        resp = client.post(
            "/api/v1/projects/seed/HONOR-ELA/promote", headers=admin_headers
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["project_key"] == "HONOR-ELA"
        assert data["source"] == "USER"
        assert data["device_count"] == 2
        assert data["match_models"] == ["MLD_LX2", "MLD_LX3"]
        assert data["customer"] == "荣耀"

        # 就地转换：同一行身份；设备归属 = 成员行（派生，device.project_id 已删）
        db_session.refresh(seed)
        assert seed.source == "USER"
        assert seed.status == "ACTIVE"
        from backend.models.project_model import ProjectModel

        members = [
            r.match_value
            for r in db_session.query(ProjectModel)
            .filter_by(project_id=seed.id, is_active=True)
            .order_by(ProjectModel.match_value)
            .all()
        ]
        assert members == ["MLD_LX2", "MLD_LX3"]

        # 列表里出现（「设备行显示归属它、筛选下拉里却没有它」消除）
        listed = client.get("/api/v1/projects", headers=admin_headers).json()["data"]
        assert any(p["project_key"] == "HONOR-ELA" for p in listed)

    def test_promote_legacy_rejected(self, client, admin_headers, project_legacy):
        resp = client.post(
            "/api/v1/projects/seed/LEGACY/promote", headers=admin_headers
        )
        assert resp.status_code == 422
        assert "fallback" in resp.json()["detail"]

    def test_promote_archived_rejected(self, client, db_session, admin_headers):
        self._seed_project(db_session, status="ARCHIVED")
        resp = client.post(
            "/api/v1/projects/seed/HONOR-ELA/promote", headers=admin_headers
        )
        assert resp.status_code == 409
        assert "archived" in resp.json()["detail"]

    def test_promote_twice_404(self, client, db_session, admin_headers):
        """转正后行已非 SEED——重复调用 404（幂等语义）。"""
        self._seed_project(db_session)
        first = client.post(
            "/api/v1/projects/seed/HONOR-ELA/promote", headers=admin_headers
        )
        assert first.status_code == 200
        second = client.post(
            "/api/v1/projects/seed/HONOR-ELA/promote", headers=admin_headers
        )
        assert second.status_code == 404

    def test_promote_unknown_404(self, client, admin_headers):
        resp = client.post(
            "/api/v1/projects/seed/NOPE/promote", headers=admin_headers
        )
        assert resp.status_code == 404

    def test_promote_forbidden_for_non_admin(self, client, db_session, auth_headers):
        self._seed_project(db_session)
        resp = client.post(
            "/api/v1/projects/seed/HONOR-ELA/promote", headers=auth_headers
        )
        assert resp.status_code == 403


class TestBulkAssignProject:
    def test_assign_updates_devices_and_records_audit(
        self, client, db_session, project_a, project_legacy, admin_headers
    ):
        from backend.models.project_model import ProjectModel

        d1 = _make_device(db_session, "s-bulk-1", None, model="BULK_M1")
        d2 = _make_device(db_session, "s-bulk-2", None, model="BULK_M2")

        resp = client.post(
            "/api/v1/devices/bulk-project",
            headers=admin_headers,
            json={"project_key": "proj-a", "device_ids": [d1.id, d2.id]},
        )
        assert resp.status_code == 200
        returned = {d["serial"]: d for d in resp.json()["data"]}
        # v2.5：派生归属——响应 project_key 来自成员行
        assert returned["s-bulk-1"]["project_key"] == "proj-a"
        assert returned["s-bulk-2"]["project_key"] == "proj-a"

        # 归属 = 型号成员行（不再写 device 列）
        members = db_session.query(ProjectModel).filter(
            ProjectModel.project_id == project_a.id,
            ProjectModel.is_active.is_(True),
        ).all()
        assert sorted(m.match_value for m in members) == ["BULK_M1", "BULK_M2"]

        # audit：一条汇总（bulk_assign_project_models）
        audits = (
            db_session.query(AuditLog)
            .filter(AuditLog.action == "bulk_assign_project_models")
            .all()
        )
        assert len(audits) == 1
        assert audits[0].details["models"] == ["BULK_M1", "BULK_M2"]

    def test_idempotent_skip_no_audit(
        self, client, db_session, project_a, admin_headers
    ):

        d1 = _make_device(db_session, "s-bulk-idem", project_a, model="BULK_IDEM")
        # 成员行已存在（helper 建的） = 幂等 no-op（不记 audit）
        resp = client.post(
            "/api/v1/devices/bulk-project",
            headers=admin_headers,
            json={"project_key": "proj-a", "device_ids": [d1.id]},
        )
        assert resp.status_code == 200
        audits = (
            db_session.query(AuditLog)
            .filter(AuditLog.action == "bulk_assign_project_models")
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
        assert resp.status_code == 404  # 整体拒绝，防部分成功（M3 后无 device.project_id 可断言）

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
        from backend.models.project_model import ProjectModel

        db_session.add(ProjectModel(
            project_id=project_a.id, match_value="F_MODEL"))
        db_session.commit()
        _make_device(db_session, "s-filt-a", project_a, model="F_MODEL")
        _make_device(db_session, "s-filt-legacy", project_legacy, model="L_MODEL")
        _make_device(db_session, "s-filt-null", None, model=None)

        resp = client.get(
            "/api/v1/devices?project_key=proj-a", headers=auth_headers
        )
        assert resp.status_code == 200
        serials = {d["serial"] for d in resp.json()}
        assert serials == {"s-filt-a"}  # 未映射型号与无型号都不命中
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
        # 标签字段（PR 2 前端依赖）：PlanOut.project_key 填充
        assert resp.json()["data"][0]["project_key"] == "proj-a"

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
        # 标签字段（PR 2 前端依赖）：PlanRunDetailOut.project_key 填充
        assert resp.json()["data"][0]["project_key"] == "proj-a"

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
        # 标签字段（PR 2 前端依赖）：RecentRun.project_key 填充
        assert body["recent_runs"][0]["project_key"] == "proj-a"

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


class TestRemoveProjectRule:
    """ADR-0029 复盘：删除型号规则（规则表此前只增不减）。"""

    def test_remove_rule_records_audit(
        self, client, db_session, project_a, admin_headers
    ):
        from backend.models.project_model import ProjectModel

        db_session.add(ProjectModel(
            project_id=project_a.id, match_value="MLD_LX2"))
        db_session.commit()

        resp = client.delete(
            "/api/v1/projects/proj-a/rules/MLD_LX2", headers=admin_headers
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"] == {"project_key": "proj-a", "model": "MLD_LX2"}
        assert db_session.query(ProjectModel).filter_by(
            project_id=project_a.id, match_value="MLD_LX2",
        ).count() == 0
        audits = db_session.query(AuditLog).filter(
            AuditLog.action == "remove_project_model"
        ).all()
        assert len(audits) == 1
        assert audits[0].details["model"] == "MLD_LX2"

    def test_remove_missing_rule_404(self, client, project_a, admin_headers):
        resp = client.delete(
            "/api/v1/projects/proj-a/rules/NO_SUCH", headers=admin_headers
        )
        assert resp.status_code == 404

    def test_remove_rule_forbidden_for_non_admin(
        self, client, project_a, auth_headers
    ):
        resp = client.delete(
            "/api/v1/projects/proj-a/rules/MLD_LX2", headers=auth_headers
        )
        assert resp.status_code == 403


class TestRenameProject:
    """ADR-0029 D2 复核：项目重命名（key 是用户指定标识，admin 可改）。"""

    def test_rename_updates_key_and_records_audit(
        self, client, db_session, project_a, admin_headers
    ):
        from backend.models.project_model import ProjectModel

        db_session.add(ProjectModel(
            project_id=project_a.id, match_value="RENAME_MODEL"))
        db_session.commit()
        _make_device(db_session, "s-rename-1", project_a, model="RENAME_MODEL")
        plan = _make_plan(db_session, "plan-rename", project_a)
        _make_run(db_session, plan, status="SUCCESS", project=project_a)

        resp = client.put(
            "/api/v1/projects/proj-a/rename",
            json={"new_key": "proj-a2"},
            headers=admin_headers,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["project_key"] == "proj-a2"
        assert data["device_count"] == 1

        # 外键归属性不受 key 影响（id 是稳定身份）
        db_session.refresh(project_a)
        assert project_a.project_key == "proj-a2"
        audits = db_session.query(AuditLog).filter(
            AuditLog.action == "rename_project"
        ).all()
        assert len(audits) == 1
        assert audits[0].details["from_project_key"] == "proj-a"
        assert audits[0].details["to_project_key"] == "proj-a2"

        # 新 key 可查、旧 key 404
        assert client.get("/api/v1/projects/proj-a2", headers=admin_headers).status_code == 200
        assert client.get("/api/v1/projects/proj-a", headers=admin_headers).status_code == 404

    def test_rename_conflict_and_reserved_and_format(
        self, client, db_session, project_a, admin_headers
    ):
        other = TestProject(project_key="taken-key", display_name="t", source="USER")
        db_session.add(other)
        db_session.commit()

        assert client.put("/api/v1/projects/proj-a/rename",
                          json={"new_key": "TAKEN-KEY"}, headers=admin_headers).status_code == 409
        assert client.put("/api/v1/projects/proj-a/rename",
                          json={"new_key": "HONOR-MLD"}, headers=admin_headers).status_code == 422
        assert client.put("/api/v1/projects/proj-a/rename",
                          json={"new_key": "bad key!"}, headers=admin_headers).status_code == 422

    def test_rename_forbidden_for_non_admin(self, client, project_a, auth_headers):
        resp = client.put("/api/v1/projects/proj-a/rename",
                          json={"new_key": "hacked"}, headers=auth_headers)
        assert resp.status_code == 403


class TestCustomerDict:
    """ADR-0029 D12 — customer 字典（项目编辑下拉的数据源，列不动）。"""

    def test_list_customers_sorted(
        self, client, db_session, auth_headers
    ):
        db_session.add_all([
            Customer(key="传音", display_name="传音", sort_order=3),
            Customer(key="荣耀", display_name="荣耀", sort_order=1),
            Customer(key="中兴", display_name="中兴", sort_order=2),
        ])
        db_session.commit()
        resp = client.get("/api/v1/projects/customers", headers=auth_headers)
        assert resp.status_code == 200
        rows = resp.json()["data"]
        assert [r["key"] for r in rows] == ["荣耀", "中兴", "传音"]
        assert rows[0]["display_name"] == "荣耀"
        assert set(rows[0]) == {"key", "display_name", "sort_order"}

    def test_customers_not_captured_by_project_key_route(
        self, client, auth_headers
    ):
        # 回归：/customers 静态段注册在 /{project_key} 之前，不得被捕获为 key
        resp = client.get("/api/v1/projects/customers", headers=auth_headers)
        assert resp.status_code == 200

    def test_requires_auth(self, client):
        assert client.get("/api/v1/projects/customers").status_code == 401


class TestArchiveGuards:
    """#644 P1-4 — 归档守卫补齐 + 解档端点 + SEED 退场。"""

    def _archive(self, client, admin_headers, key="proj-a"):
        resp = client.post(f"/api/v1/projects/{key}/archive", headers=admin_headers)
        assert resp.status_code == 200, resp.text

    def test_archived_project_read_only_endpoints(
        self, client, admin_headers, db_session, project_a
    ):
        from backend.models.project_model import ProjectModel

        db_session.add(ProjectModel(project_id=project_a.id, match_value="MLD_LX2"))
        db_session.commit()
        self._archive(client, admin_headers)

        rename = client.put(
            "/api/v1/projects/proj-a/rename",
            headers=admin_headers, json={"new_key": "NEW-KEY"},
        )
        assert rename.status_code == 409

        preview = client.post(
            "/api/v1/projects/proj-a/map/preview",
            headers=admin_headers, json={"models": ["MLD_LX3"]},
        )
        assert preview.status_code == 409

        apply = client.post(
            "/api/v1/projects/proj-a/map/apply",
            headers=admin_headers, json={"models": ["MLD_LX3"]},
        )
        assert apply.status_code == 409

        remove = client.delete(
            "/api/v1/projects/proj-a/rules/MLD_LX2", headers=admin_headers
        )
        assert remove.status_code == 409

    def test_unarchive_restores_and_records_audit(
        self, client, admin_headers, db_session, project_a
    ):
        self._archive(client, admin_headers)
        resp = client.post(
            "/api/v1/projects/proj-a/unarchive", headers=admin_headers
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["status"] == "ACTIVE"
        audits = db_session.query(AuditLog).filter(
            AuditLog.action == "unarchive_project"
        ).all()
        assert len(audits) == 1
        assert audits[0].details["to_status"] == "ACTIVE"
        # 解档后可继续改名（只读守卫解除）
        rename = client.put(
            "/api/v1/projects/proj-a/rename",
            headers=admin_headers, json={"new_key": "NEW-KEY"},
        )
        assert rename.status_code == 200

    def test_unarchive_not_archived_409(self, client, admin_headers, project_a):
        resp = client.post(
            "/api/v1/projects/proj-a/unarchive", headers=admin_headers
        )
        assert resp.status_code == 409

    def test_unarchive_forbidden_for_non_admin(
        self, client, db_session, project_a, auth_headers
    ):
        project_a.status = "ARCHIVED"
        db_session.commit()
        resp = client.post(
            "/api/v1/projects/proj-a/unarchive", headers=auth_headers
        )
        assert resp.status_code == 403

    def test_seed_list_defaults_active(
        self, client, auth_headers, admin_headers, project_legacy
    ):
        # 待转正队列默认只列 ACTIVE；归档（放弃）后退出队列
        seed = client.get("/api/v1/projects?source=seed", headers=auth_headers)
        assert {p["project_key"] for p in seed.json()["data"]} == {"LEGACY"}

        self._archive(client, admin_headers, key="LEGACY")

        active = client.get("/api/v1/projects?source=seed", headers=auth_headers)
        assert active.json()["data"] == []
        archived = client.get(
            "/api/v1/projects?source=seed&status=ARCHIVED", headers=auth_headers
        )
        assert {p["project_key"] for p in archived.json()["data"]} == {"LEGACY"}
