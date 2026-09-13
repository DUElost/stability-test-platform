"""#1520 垂直切片第二刀：项目型号映射写路径的服务层直测。

覆盖 map/preview、map/apply、rules/{model} 删除三面在**无 HTTP 层**下的行为：
冲突判定、SEED 让位、大小写变体收敛到设备事实原值、404/409/422 语义与审计。
API 级回归仍由 `backend/tests/api/test_project_routes.py`（75 例）覆盖。
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from backend.models.audit import AuditLog
from backend.models.host import Device
from backend.models.project import TestProject
from backend.models.project_model import ProjectModel
from backend.services.project_mapping import (
    apply_project_mapping,
    normalize_models,
    preview_project_mapping,
    remove_project_mapping_rule,
)

_ACTOR = {"actor_id": None, "actor_username": "tester"}


def _project(db_session, key: str, *, source: str = "USER") -> TestProject:
    project = TestProject(project_key=key, display_name=key, source=source)
    db_session.add(project)
    db_session.commit()
    return project


def _device(db_session, serial: str, model: str) -> Device:
    device = Device(serial=serial, model=model, status="ONLINE")
    db_session.add(device)
    db_session.commit()
    return device


def _rule(db_session, project: TestProject, match_value: str, *, active: bool = True) -> ProjectModel:
    rule = ProjectModel(
        project_id=project.id, match_value=match_value, is_active=active,
    )
    db_session.add(rule)
    db_session.commit()
    return rule


def _audit_actions(db_session, project_id: int) -> list[str]:
    return [
        row.action
        for row in db_session.execute(
            select(AuditLog).where(AuditLog.resource_id == str(project_id))
        ).scalars()
    ]


class TestNormalizeModels:
    def test_dedup_strip_upper(self):
        assert normalize_models([" a57 ", "A57", "", "  ", "b-1"]) == ["A57", "B-1"]


class TestPreview:
    def test_empty_models_422(self, db_session):
        project = _project(db_session, "pm-empty")
        with pytest.raises(HTTPException) as exc:
            preview_project_mapping(
                db_session, project_key=project.project_key, models=[], reassign_conflicts=False,
            )
        assert exc.value.status_code == 422

    def test_user_conflict_reported_and_bypassed_with_reassign(self, db_session):
        owner = _project(db_session, "pm-owner")
        target = _project(db_session, "pm-target")
        _device(db_session, "pm-d1", "A57")
        _rule(db_session, owner, "A57")

        conflicted = preview_project_mapping(
            db_session, project_key=target.project_key, models=["a57"],
            reassign_conflicts=False,
        )
        assert conflicted.will_assign == 0
        assert [c.from_project_key for c in conflicted.conflicts] == ["pm-owner"]

        reassigned = preview_project_mapping(
            db_session, project_key=target.project_key, models=["a57"],
            reassign_conflicts=True,
        )
        assert reassigned.will_assign == 1
        assert reassigned.conflicts == []

    def test_unknown_model_reported(self, db_session):
        target = _project(db_session, "pm-unknown")
        _device(db_session, "pm-d2", "A57")

        preview = preview_project_mapping(
            db_session, project_key=target.project_key, models=["A57", "ZZZ-9"],
            reassign_conflicts=False,
        )

        assert preview.unknown_models == ["ZZZ-9"]
        assert preview.will_assign == 1


class TestApply:
    def test_seed_rule_yields_and_writes_device_fact_value(self, db_session):
        """SEED 占用不算冲突（让位）；新行写设备事实原值（join 全等命中）。"""
        seed = _project(db_session, "pm-seed", source="SEED")
        target = _project(db_session, "pm-apply")
        _device(db_session, "pm-d3", "Mld_Lx2")
        seed_rule = _rule(db_session, seed, "MLD_LX2")

        preview = apply_project_mapping(
            db_session, project_key=target.project_key, models=["mld_lx2"],
            reassign_conflicts=False, **_ACTOR,
        )

        assert preview.will_assign == 1
        db_session.expire_all()
        assert db_session.get(ProjectModel, seed_rule.id).is_active is False
        new_rule = db_session.execute(
            select(ProjectModel).where(
                ProjectModel.project_id == target.id,
                ProjectModel.is_active.is_(True),
            )
        ).scalar_one()
        # 归一值是 MLD_LX2，但成员行必须写设备事实原值 Mld_Lx2
        assert new_rule.match_value == "Mld_Lx2"
        assert _audit_actions(db_session, target.id) == ["apply_project_model"]

    def test_case_variant_row_converges_to_device_fact(self, db_session):
        """#752：同项目混合大小写行必须收敛到设备事实原值（否则 join 全等 miss）。"""
        target = _project(db_session, "pm-case")
        _device(db_session, "pm-d4", "Infinix_X1102D")
        existing = _rule(db_session, target, "INFINIX_X1102D")

        apply_project_mapping(
            db_session, project_key=target.project_key, models=["infinix_x1102d"],
            reassign_conflicts=False, **_ACTOR,
        )

        db_session.expire_all()
        assert db_session.get(ProjectModel, existing.id).match_value == "Infinix_X1102D"

    def test_user_conflict_without_reassign_409(self, db_session):
        owner = _project(db_session, "pm-owner2")
        target = _project(db_session, "pm-target2")
        _device(db_session, "pm-d5", "C99")
        _rule(db_session, owner, "C99")

        with pytest.raises(HTTPException) as exc:
            apply_project_mapping(
                db_session, project_key=target.project_key, models=["C99"],
                reassign_conflicts=False, **_ACTOR,
            )
        assert exc.value.status_code == 409


class TestRemoveRule:
    def test_missing_active_rule_404(self, db_session):
        target = _project(db_session, "pm-rm404")
        with pytest.raises(HTTPException) as exc:
            remove_project_mapping_rule(
                db_session, project_key=target.project_key, model="NOPE", **_ACTOR,
            )
        assert exc.value.status_code == 404

    def test_removes_active_rule_with_audit(self, db_session):
        target = _project(db_session, "pm-rm")
        rule = _rule(db_session, target, "D55")

        removed = remove_project_mapping_rule(
            db_session, project_key=target.project_key, model="D55", **_ACTOR,
        )

        assert removed == {"project_key": "pm-rm", "model": "D55"}
        db_session.expire_all()
        assert db_session.get(ProjectModel, rule.id) is None
        assert _audit_actions(db_session, target.id) == ["remove_project_model"]
