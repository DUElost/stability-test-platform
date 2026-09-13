"""#1520 垂直切片：项目登记簿业务规则的服务层直测。

这些断言刻意**不经过 TestClient**——切分前业务规则与 HTTP 请求/响应对象耦合在
同一函数体，「无字段变更不提交/不发事件」「归档只读」这类分支只能靠 API 级用例
间接覆盖；下沉到 `services/project_registry.py` 后可直接驱动并断言副作用。

API 级行为回归仍由 `backend/tests/api/test_project_routes.py`（75 例）覆盖，
本文件只补「服务边界」这一层。
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from backend.models.audit import AuditLog
from backend.models.project import TestProject
from backend.services.project_registry import (
    archive_project_entry,
    create_project_entry,
    rename_project_entry,
    unarchive_project_entry,
    update_project_facets,
)

@pytest.fixture
def actor(admin_user):
    """审计写入有 users.id 外键——actor 必须是真实用户行。"""
    return {"actor_id": admin_user.id, "actor_username": admin_user.username}


def _make_project(db_session, key: str = "prj-a", **kwargs) -> TestProject:
    project = TestProject(
        project_key=key,
        display_name=kwargs.pop("display_name", "A"),
        source=kwargs.pop("source", "USER"),
        **kwargs,
    )
    db_session.add(project)
    db_session.commit()
    return project


def _audit_actions(db_session, resource_id: int) -> list[str]:
    return [
        row.action
        for row in db_session.execute(
            select(AuditLog).where(AuditLog.resource_id == str(resource_id))
        ).scalars()
    ]


class TestCreate:
    def test_rejects_reserved_seed_key(self, db_session, actor):
        with pytest.raises(HTTPException) as exc:
            create_project_entry(
                db_session, project_key="HONOR-MLD", display_name="x",
                customer=None, jira_project_key=None, **actor,
            )
        assert exc.value.status_code == 422

    def test_rejects_case_variant_duplicate(self, db_session, actor):
        _make_project(db_session, "PRJ-A")
        with pytest.raises(HTTPException) as exc:
            create_project_entry(
                db_session, project_key="prj-a", display_name="x",
                customer=None, jira_project_key=None, **actor,
            )
        assert exc.value.status_code == 409

    def test_creates_user_project_with_audit(self, db_session, actor):
        project = create_project_entry(
            db_session, project_key="prj-new", display_name="  新项目  ",
            customer="CustX", jira_project_key=None, **actor,
        )
        assert project.source == "USER"
        assert project.status == "ACTIVE"
        assert project.display_name == "新项目"  # strip 语义在服务内
        assert _audit_actions(db_session, project.id) == ["create_project"]


class TestUpdateFacets:
    def test_no_change_returns_without_commit_or_audit(self, db_session, actor):
        project = _make_project(db_session, "prj-noop", display_name="同名")
        updated_at = project.updated_at

        result = update_project_facets(
            db_session, project_key="prj-noop",
            provided={"display_name": "同名"}, **actor,
        )

        assert result.id == project.id
        db_session.refresh(result)
        assert result.updated_at == updated_at  # 未提交、未触碰 updated_at
        assert _audit_actions(db_session, project.id) == []

    def test_changed_field_writes_per_field_audit(self, db_session, actor):
        project = _make_project(db_session, "prj-upd", display_name="旧名")
        update_project_facets(
            db_session, project_key="prj-upd",
            provided={"display_name": "新名", "customer": "CustZ"}, **actor,
        )
        db_session.refresh(project)
        assert project.display_name == "新名"
        assert project.customer == "CustZ"
        assert _audit_actions(db_session, project.id) == ["update_project", "update_project"]

    def test_archived_project_is_not_editable(self, db_session, actor):
        _make_project(db_session, "prj-frozen", status="ARCHIVED")
        with pytest.raises(HTTPException) as exc:
            update_project_facets(
                db_session, project_key="prj-frozen",
                provided={"display_name": "x"}, **actor,
            )
        assert exc.value.status_code == 409
        assert "archived" in exc.value.detail


class TestRename:
    def test_rename_writes_from_to_audit_and_updates_key(self, db_session, actor):
        project = _make_project(db_session, "prj-old")
        rename_project_entry(
            db_session, project_key="prj-old", new_key="prj-new", **actor,
        )
        db_session.refresh(project)
        assert project.project_key == "prj-new"
        assert _audit_actions(db_session, project.id) == ["rename_project"]

    def test_rename_to_existing_key_conflicts(self, db_session, actor):
        _make_project(db_session, "prj-one")
        _make_project(db_session, "prj-two")
        with pytest.raises(HTTPException) as exc:
            rename_project_entry(
                db_session, project_key="prj-one", new_key="prj-two", **actor,
            )
        assert exc.value.status_code == 409

    def test_seed_project_cannot_be_renamed(self, db_session, actor):
        _make_project(db_session, "seed-label", source="SEED")
        with pytest.raises(HTTPException) as exc:
            rename_project_entry(
                db_session, project_key="seed-label", new_key="brand-new", **actor,
            )
        assert exc.value.status_code == 422


class TestArchiveCycle:
    def test_archive_then_unarchive_roundtrip(self, db_session, actor):
        project = _make_project(db_session, "prj-cycle")
        archive_project_entry(db_session, project_key="prj-cycle", **actor)
        db_session.refresh(project)
        assert project.status == "ARCHIVED"

        unarchive_project_entry(db_session, project_key="prj-cycle", **actor)
        db_session.refresh(project)
        assert project.status == "ACTIVE"
        assert _audit_actions(db_session, project.id) == [
            "archive_project", "unarchive_project",
        ]

    def test_archive_twice_conflicts(self, db_session, actor):
        _make_project(db_session, "prj-double", status="ARCHIVED")
        with pytest.raises(HTTPException) as exc:
            archive_project_entry(db_session, project_key="prj-double", **actor)
        assert exc.value.status_code == 409

    def test_unarchive_active_project_conflicts(self, db_session, actor):
        _make_project(db_session, "prj-active")
        with pytest.raises(HTTPException) as exc:
            unarchive_project_entry(db_session, project_key="prj-active", **actor)
        assert exc.value.status_code == 409
