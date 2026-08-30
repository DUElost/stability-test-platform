"""ProjectModel + project_attribution 解析层测试（ADR-0029 P1）。"""

import pytest
from sqlalchemy.exc import IntegrityError

from backend.models.project import TestProject
from backend.models.project_model import ProjectModel
from backend.services.project_attribution import resolve_project_id


@pytest.fixture
def _rule_project(db_session):
    project = TestProject(project_key="RULE-A", display_name="rule", source="USER")
    db_session.add(project)
    db_session.commit()
    return project


class TestResolveProjectId:
    def test_hit_returns_project_id(self, db_session, _rule_project):
        db_session.add(ProjectModel(
            project_id=_rule_project.id, match_value="MLD_LX2"))
        db_session.commit()
        assert resolve_project_id(db_session, "MLD_LX2") == _rule_project.id

    def test_no_rule_returns_none(self, db_session):
        assert resolve_project_id(db_session, "Z2581") is None

    def test_blank_model_returns_none(self, db_session, _rule_project):
        db_session.add(ProjectModel(
            project_id=_rule_project.id, match_value="MLD_LX2"))
        db_session.commit()
        assert resolve_project_id(db_session, None) is None
        assert resolve_project_id(db_session, "") is None

    def test_inactive_rule_not_matched(self, db_session, _rule_project):
        db_session.add(ProjectModel(
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
        db_session.add(ProjectModel(
            project_id=_rule_project.id, match_value="MLD_LX2"))
        db_session.commit()
        with pytest.raises(IntegrityError):  # SQLite/PG 都报唯一冲突
            db_session.add(ProjectModel(
                project_id=other.id, match_value="MLD_LX2"))
            db_session.commit()
        db_session.rollback()
