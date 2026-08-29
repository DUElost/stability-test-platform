"""ProjectDeviceRule + project_attribution 解析层测试（ADR-0029 P1）。"""

import pytest
from sqlalchemy.exc import IntegrityError

from backend.models.project import TestProject
from backend.models.project_rule import ProjectDeviceRule
from backend.services.project_attribution import (
    apply_attribution,
    resolve_project_id,
)


@pytest.fixture
def _rule_project(db_session):
    project = TestProject(project_key="RULE-A", display_name="rule", source="USER")
    db_session.add(project)
    db_session.commit()
    return project


class TestResolveProjectId:
    def test_hit_returns_project_id(self, db_session, _rule_project):
        db_session.add(ProjectDeviceRule(
            project_id=_rule_project.id, match_value="MLD_LX2"))
        db_session.commit()
        assert resolve_project_id(db_session, "MLD_LX2") == _rule_project.id

    def test_no_rule_returns_none(self, db_session):
        assert resolve_project_id(db_session, "Z2581") is None

    def test_blank_model_returns_none(self, db_session, _rule_project):
        db_session.add(ProjectDeviceRule(
            project_id=_rule_project.id, match_value="MLD_LX2"))
        db_session.commit()
        assert resolve_project_id(db_session, None) is None
        assert resolve_project_id(db_session, "") is None

    def test_inactive_rule_not_matched(self, db_session, _rule_project):
        db_session.add(ProjectDeviceRule(
            project_id=_rule_project.id, match_value="MLD_LX2",
            is_active=False))
        db_session.commit()
        assert resolve_project_id(db_session, "MLD_LX2") is None

    def test_same_model_cannot_rule_to_two_projects(
        self, db_session, _rule_project
    ):
        """活跃唯一索引：同型号双归属直接 IntegrityError（DB 层保证）。"""
        other = TestProject(project_key="RULE-B", display_name="b", source="USER")
        db_session.add(other)
        db_session.commit()
        db_session.add(ProjectDeviceRule(
            project_id=_rule_project.id, match_value="MLD_LX2"))
        db_session.commit()
        with pytest.raises(IntegrityError):  # SQLite/PG 都报唯一冲突
            db_session.add(ProjectDeviceRule(
                project_id=other.id, match_value="MLD_LX2"))
            db_session.commit()
        db_session.rollback()


class TestApplyAttribution:
    def _device(self, db_session, model="MLD_LX2", project_id=None, pinned=False):
        from backend.models.host import Device

        device = Device(
            serial=f"S-{model}-{abs(hash(model)) % 1000}",
            model=model,
            project_id=project_id,
            project_pinned=pinned,
        )
        db_session.add(device)
        db_session.flush()
        return device

    def test_applies_when_unattributed(self, db_session, _rule_project):
        db_session.add(ProjectDeviceRule(
            project_id=_rule_project.id, match_value="MLD_LX2"))
        db_session.commit()
        device = self._device(db_session)
        assert apply_attribution(db_session, device) is True
        assert device.project_id == _rule_project.id

    def test_no_change_when_already_correct(self, db_session, _rule_project):
        db_session.add(ProjectDeviceRule(
            project_id=_rule_project.id, match_value="MLD_LX2"))
        db_session.commit()
        device = self._device(db_session, project_id=_rule_project.id)
        assert apply_attribution(db_session, device) is False
        assert device.project_id == _rule_project.id

    def test_pinned_never_overwritten(self, db_session, _rule_project):
        db_session.add(ProjectDeviceRule(
            project_id=_rule_project.id, match_value="MLD_LX2"))
        db_session.commit()
        device = self._device(db_session, project_id=None, pinned=True)
        assert apply_attribution(db_session, device) is False
        assert device.project_id is None

    def test_no_rule_keeps_existing_attribution(
        self, db_session, _rule_project
    ):
        """未命中不抹除已有归属——归属错了改规则/改钉住，不是心跳清空。"""
        device = self._device(db_session, project_id=_rule_project.id)
        assert apply_attribution(db_session, device) is False
        assert device.project_id == _rule_project.id
