"""#1520 垂直切片第三刀：项目/Fleet 读侧聚合的服务层直测。

覆盖型号级聚合口径（ADR-0029 v2.5 D10 派生）、成员型号列表、项目汇总聚合与
inventory 摘要计数；API 级回归仍由 `backend/tests/api/test_project_routes.py`
（75 例）覆盖。
"""

from __future__ import annotations

from datetime import datetime, timezone

from backend.models.host import Device
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun
from backend.models.project import TestProject
from backend.models.project_model import ProjectModel
from backend.services.project_inventory import (
    aggregate_inventory,
    inventory_summary,
    load_inventory,
    platforms_map,
    rule_values_for_project,
    summary_rows,
    summary_rows_for,
)

_NOW = datetime.now(timezone.utc)


def _project(db_session, key: str, *, source: str = "USER") -> TestProject:
    project = TestProject(project_key=key, display_name=key, source=source)
    db_session.add(project)
    db_session.commit()
    return project


def _rule(db_session, project: TestProject, model: str, *, active: bool = True) -> None:
    db_session.add(ProjectModel(
        project_id=project.id, match_value=model, is_active=active,
    ))
    db_session.commit()


def _device(db_session, serial: str, model: str, *, platform: str = "MTK", status: str = "ONLINE") -> Device:
    device = Device(serial=serial, model=model, platform=platform, status=status)
    db_session.add(device)
    db_session.commit()
    return device


class TestAggregateInventory:
    def test_mapped_and_unmapped_device_counts(self):
        rows = [("A57", "MTK"), ("A57", "UNISOC"), ("ZZ9", None)]
        items = aggregate_inventory(rows, model_to_projects={"A57": {"proj-a"}})
        by_model = {item.model: item for item in items}

        assert by_model["A57"].device_count == 2
        assert by_model["A57"].mapped_project_keys == ["proj-a"]
        assert by_model["A57"].unassigned_device_count == 0
        assert by_model["A57"].platforms == ["MTK", "UNISOC"]
        assert by_model["ZZ9"].mapped_project_keys == []
        assert by_model["ZZ9"].unassigned_device_count == 1

    def test_sorted_by_device_count_desc(self):
        items = aggregate_inventory(
            [("M1", None), ("M2", None), ("M2", None)],
            model_to_projects={},
        )
        assert [item.model for item in items] == ["M2", "M1"]


class TestProjectReadAggregates:
    def test_summary_rows_count_devices_and_active_runs(self, db_session):
        project = _project(db_session, "inv-p1")
        plan = Plan(name="inv-plan")
        db_session.add(plan)
        db_session.flush()
        _rule(db_session, project, "A57")
        _device(db_session, "inv-d1", "A57")
        _device(db_session, "inv-d2", "A57")
        db_session.add_all([
            PlanRun(plan_id=plan.id, project_id=project.id, status="RUNNING",
                    plan_snapshot={}, run_type="MANUAL", started_at=_NOW),
            PlanRun(plan_id=plan.id, project_id=project.id, status="SUCCESS",
                    plan_snapshot={}, run_type="MANUAL", started_at=_NOW),
        ])
        db_session.commit()

        rows = summary_rows_for(db_session, [project.id])
        assert rows[project.id] == (2, 1)  # 2 台设备、1 个在途 run
        assert summary_rows(db_session)[project.id] == (2, 1)

    def test_platforms_derived_from_member_models(self, db_session):
        project = _project(db_session, "inv-p2")
        _rule(db_session, project, "A57")
        _device(db_session, "inv-d3", "A57", platform="MTK")

        assert platforms_map(db_session, [project.id]) == {project.id: ["MTK"]}
        assert platforms_map(db_session, []) == {}

    def test_rule_values_only_active_members(self, db_session):
        project = _project(db_session, "inv-p3")
        _rule(db_session, project, "B1")
        _rule(db_session, project, "A1")
        _rule(db_session, project, "Z9", active=False)

        assert rule_values_for_project(db_session, project.id) == ["A1", "B1"]


class TestInventorySummary:
    def test_counts_split_user_mapped_vs_unmapped(self, db_session):
        project = _project(db_session, "inv-p4")
        seed = _project(db_session, "inv-seed", source="SEED")
        _rule(db_session, project, "A57")       # USER 成员 → 计入 mapped
        _rule(db_session, seed, "SEEDM")        # SEED 成员 → 不计入 mapped
        _device(db_session, "inv-d4", "A57")
        _device(db_session, "inv-d5", "SEEDM")
        _device(db_session, "inv-d6", "NOPE")

        items = load_inventory(db_session)
        summary = inventory_summary(db_session, items)

        assert summary.total_devices == 3
        assert summary.user_mapped_devices == 1     # 只有 A57 算 USER 映射
        assert summary.distinct_models == 3
        assert sorted(summary.unmapped_models) == ["NOPE", "SEEDM"]
        assert summary.unassigned_devices == 2      # SEEDM + NOPE
