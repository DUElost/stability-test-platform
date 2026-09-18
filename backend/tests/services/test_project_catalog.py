"""#1520 垂直切片：projects 读侧 catalog 装配的服务层直测。

API 级回归仍由 `backend/tests/api/test_project_routes.py` 覆盖。
"""

from __future__ import annotations

from datetime import datetime, timezone

from backend.models.host import Device
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun
from backend.models.project import Customer, TestProject
from backend.models.project_model import ProjectModel
from backend.services.project_catalog import (
    build_project_detail,
    fill_project_summary,
    fill_promoted_summary,
    list_customer_entries,
    list_project_model_coverage,
    list_project_summaries,
)

_NOW = datetime.now(timezone.utc)


def _project(db_session, key: str, *, source: str = "USER", status: str = "ACTIVE") -> TestProject:
    project = TestProject(
        project_key=key, display_name=key, source=source, status=status,
    )
    db_session.add(project)
    db_session.commit()
    return project


def _rule(db_session, project: TestProject, model: str) -> None:
    db_session.add(ProjectModel(
        project_id=project.id, match_value=model, is_active=True,
    ))
    db_session.commit()


def _device(db_session, serial: str, model: str, *, platform: str = "MTK") -> Device:
    device = Device(serial=serial, model=model, platform=platform, status="ONLINE")
    db_session.add(device)
    db_session.commit()
    return device


class TestFillProjectSummary:
    def test_fills_counts_models_and_platforms(self, db_session):
        project = _project(db_session, "cat-p1")
        _rule(db_session, project, "A57")
        _device(db_session, "cat-d1", "A57", platform="MTK")
        plan = Plan(name="cat-plan")
        db_session.add(plan)
        db_session.flush()
        db_session.add(PlanRun(
            plan_id=plan.id, project_id=project.id, status="RUNNING",
            plan_snapshot={}, run_type="MANUAL", started_at=_NOW,
        ))
        db_session.commit()

        out = fill_project_summary(db_session, project)
        assert out.device_count == 1
        assert out.running_run_count == 1
        assert out.match_models == ["A57"]
        assert out.platforms == ["MTK"]

    def test_promoted_summary_omits_platforms(self, db_session):
        project = _project(db_session, "cat-p2")
        _rule(db_session, project, "A57")
        _device(db_session, "cat-d2", "A57", platform="MTK")

        out = fill_promoted_summary(db_session, project)
        assert out.device_count == 1
        assert out.match_models == ["A57"]
        # 历史 promote 口径：不填 platforms（默认空）
        assert not out.platforms


class TestListProjectSummaries:
    def test_user_source_excludes_seed(self, db_session):
        _project(db_session, "cat-user", source="USER")
        _project(db_session, "cat-seed", source="SEED")

        keys = [p.project_key for p in list_project_summaries(db_session, source="user")]
        assert "cat-user" in keys
        assert "cat-seed" not in keys

    def test_seed_default_hides_archived(self, db_session):
        _project(db_session, "cat-seed-a", source="SEED", status="ACTIVE")
        _project(db_session, "cat-seed-z", source="SEED", status="ARCHIVED")

        keys = [p.project_key for p in list_project_summaries(db_session, source="seed")]
        assert keys == ["cat-seed-a"]


class TestCustomersAndModelsAndDetail:
    def test_list_customer_entries_ordered(self, db_session):
        db_session.add_all([
            Customer(key="B", display_name="Bee", sort_order=2),
            Customer(key="A", display_name="Aye", sort_order=1),
        ])
        db_session.commit()
        rows = list_customer_entries(db_session)
        assert [r["key"] for r in rows] == ["A", "B"]

    def test_model_coverage_and_detail(self, db_session):
        project = _project(db_session, "cat-p3")
        _rule(db_session, project, "A57")
        _device(db_session, "cat-d3", "A57", platform="MTK")
        _device(db_session, "cat-d4", "A57", platform="UNISOC")
        plan = Plan(name="cat-plan-2", project_id=project.id)
        db_session.add(plan)
        db_session.flush()
        db_session.add(PlanRun(
            plan_id=plan.id, project_id=project.id, status="SUCCESS",
            plan_snapshot={}, run_type="MANUAL", started_at=_NOW,
        ))
        db_session.commit()

        coverage = list_project_model_coverage(db_session, "cat-p3")
        assert len(coverage) == 1
        assert coverage[0].model == "A57"
        assert coverage[0].device_count == 2
        assert set(coverage[0].platforms) == {"MTK", "UNISOC"}

        detail = build_project_detail(db_session, "cat-p3")
        assert detail.device_count == 2
        assert detail.plan_count == 1
        assert detail.total_run_count == 1
        assert detail.platforms == ["MTK", "UNISOC"]
        assert len(detail.recent_runs) == 1
