"""ADR-0067 U1 — TestProject.log_profile 服务层校验、版本递增、审计与守卫测试（#4182）。"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from backend.models.audit import AuditLog
from backend.models.project import TestProject
from backend.services.errors import ServiceError
from backend.api.schemas.project_log_profile import (
    ProjectLogProfile,
    normalize_log_profile,
)
from backend.services.project_registry import update_project_facets


@pytest.fixture
def actor(admin_user):
    return {"actor_id": admin_user.id, "actor_username": admin_user.username}


def _make_project(db_session, key: str = "prj-lp", **kwargs) -> TestProject:
    project = TestProject(
        project_key=key,
        display_name=kwargs.pop("display_name", "Log Profile Project"),
        source=kwargs.pop("source", "USER"),
        **kwargs,
    )
    db_session.add(project)
    db_session.commit()
    return project


def _audits(db_session, resource_id: int) -> list[AuditLog]:
    return list(
        db_session.execute(
            select(AuditLog)
            .where(AuditLog.resource_id == str(resource_id))
            .order_by(AuditLog.id)
        ).scalars()
    )


class TestLogProfileWhitelist:
    def test_accepts_all_seven_valid_keys_and_dedups_streams(self):
        normalized = normalize_log_profile({
            "mobilelog_size_mb": 256,
            "mtk_auto_start": True,
            "mtk_sublog_cmd": "set_sublog_4_5_0",
            "aee_mode": "3",
            "ylog_size_mb": 10240,
            "ylog_rotate": False,
            "ylog_required_streams": ["Android", "kernel", "uboot", "ANDROID", "lastlog"],
        })
        assert normalized == {
            "mobilelog_size_mb": 256,
            "mtk_auto_start": True,
            "mtk_sublog_cmd": "set_sublog_4_5_0",
            "aee_mode": "3",
            "ylog_size_mb": 10240,
            "ylog_rotate": False,
            "ylog_required_streams": ["android", "kernel", "uboot", "lastlog"],
        }

    def test_empty_and_all_null_normalize_to_none(self):
        assert normalize_log_profile(None) is None
        assert normalize_log_profile({}) is None
        assert normalize_log_profile({"aee_mode": None, "mobilelog_size_mb": None}) is None

    @pytest.mark.parametrize(
        "extra_key",
        ["timezone", "sysdump_enabled", "wifi_pool_id", "apk_path", "version_prop", "unknown"],
    )
    def test_rejects_forbidden_extra_keys(self, extra_key: str):
        with pytest.raises(ValidationError):
            ProjectLogProfile.model_validate({extra_key: "x"})

    @pytest.mark.parametrize("bad_size", [0, 64, 255, 20481, True, False, "4096"])
    def test_rejects_mobilelog_size_out_of_bounds_or_bad_type(self, bad_size):
        with pytest.raises(ValidationError):
            ProjectLogProfile.model_validate({"mobilelog_size_mb": bad_size})

    @pytest.mark.parametrize("ok_size", [256, 4096, 20480])
    def test_accepts_mobilelog_size_boundaries(self, ok_size: int):
        assert normalize_log_profile({"mobilelog_size_mb": ok_size}) == {
            "mobilelog_size_mb": ok_size,
        }

    @pytest.mark.parametrize("bad_size", [0, 63, 10241, True, "4096"])
    def test_rejects_ylog_size_out_of_bounds_or_bad_type(self, bad_size):
        with pytest.raises(ValidationError):
            ProjectLogProfile.model_validate({"ylog_size_mb": bad_size})

    @pytest.mark.parametrize("ok_size", [64, 4096, 10240])
    def test_accepts_ylog_size_boundaries(self, ok_size: int):
        assert normalize_log_profile({"ylog_size_mb": ok_size}) == {
            "ylog_size_mb": ok_size,
        }

    @pytest.mark.parametrize(
        "bad_streams",
        [
            [],
            ["sysinfo"],
            ["android"],
            ["kernel"],
            ["android", "kernel", "sysinfo"],
            ["android", "kernel", "sgm"],
            "android",
            [123],
        ],
    )
    def test_rejects_missing_base_or_unverified_ylog_streams(self, bad_streams):
        with pytest.raises(ValidationError):
            ProjectLogProfile.model_validate({"ylog_required_streams": bad_streams})

    @pytest.mark.parametrize("bad_mode", ["", "   ", "abc", "123", "3;id", 3])
    def test_rejects_invalid_aee_mode(self, bad_mode):
        with pytest.raises(ValidationError):
            ProjectLogProfile.model_validate({"aee_mode": bad_mode})

    @pytest.mark.parametrize("bad_cmd", ["", "   ", "set sublog", "cmd;rm", "a" * 65, 1])
    def test_rejects_invalid_mtk_sublog_cmd(self, bad_cmd):
        with pytest.raises(ValidationError):
            ProjectLogProfile.model_validate({"mtk_sublog_cmd": bad_cmd})


class TestProjectRegistryLogProfileWrite:
    def test_updates_log_profile_increments_version_audits_and_emits(self, db_session, actor):
        project = _make_project(db_session, "prj-lp-upd")
        assert project.log_profile is None
        assert project.log_profile_version == 0

        with patch("backend.services.project_registry.emit_project_changed") as emit_mock:
            updated = update_project_facets(
                db_session,
                project_key="prj-lp-upd",
                provided={"log_profile": {"aee_mode": "3", "mobilelog_size_mb": 4096}},
                **actor,
            )

        db_session.refresh(updated)
        assert updated.log_profile == {"aee_mode": "3", "mobilelog_size_mb": 4096}
        assert updated.log_profile_version == 1
        emit_mock.assert_called_once_with(project.id, "updated")

        logs = _audits(db_session, project.id)
        assert len(logs) == 1
        assert logs[0].action == "update_project"
        assert logs[0].details["field"] == "log_profile"
        assert logs[0].details["old"] is None
        assert logs[0].details["new"] == {"aee_mode": "3", "mobilelog_size_mb": 4096}
        assert logs[0].details["old_version"] == 0
        assert logs[0].details["new_version"] == 1

    def test_noop_does_not_bump_version_audit_or_emit(self, db_session, actor):
        project = _make_project(db_session, "prj-lp-noop")

        with patch("backend.services.project_registry.emit_project_changed") as emit_mock:
            # None -> {} 与 None -> {"aee_mode": None} 均视为无实质变更
            update_project_facets(
                db_session, project_key="prj-lp-noop", provided={"log_profile": {}}, **actor,
            )
            update_project_facets(
                db_session,
                project_key="prj-lp-noop",
                provided={"log_profile": {"aee_mode": None}},
                **actor,
            )

        db_session.refresh(project)
        assert project.log_profile is None
        assert project.log_profile_version == 0
        assert _audits(db_session, project.id) == []
        emit_mock.assert_not_called()

        # 写入一次后再写相同内容也不推进版本
        update_project_facets(
            db_session, project_key="prj-lp-noop", provided={"log_profile": {"aee_mode": "3"}}, **actor,
        )
        with patch("backend.services.project_registry.emit_project_changed") as emit_mock:
            update_project_facets(
                db_session,
                project_key="prj-lp-noop",
                provided={"log_profile": {"aee_mode": "3", "ylog_size_mb": None}},
                **actor,
            )
        db_session.refresh(project)
        assert project.log_profile_version == 1
        emit_mock.assert_not_called()

    def test_clearing_log_profile_bumps_version(self, db_session, actor):
        project = _make_project(db_session, "prj-lp-clear")
        update_project_facets(
            db_session, project_key="prj-lp-clear", provided={"log_profile": {"aee_mode": "3"}}, **actor,
        )
        update_project_facets(
            db_session, project_key="prj-lp-clear", provided={"log_profile": None}, **actor,
        )
        db_session.refresh(project)
        assert project.log_profile is None
        assert project.log_profile_version == 2

    def test_seed_and_archived_projects_reject_log_profile_writes(self, db_session, actor):
        _make_project(db_session, "HONOR-MLD", source="SEED")
        with pytest.raises(ServiceError) as exc_seed:
            update_project_facets(
                db_session,
                project_key="HONOR-MLD",
                provided={"log_profile": {"aee_mode": "3"}},
                **actor,
            )
        assert exc_seed.value.status == 422

        _make_project(db_session, "prj-lp-arch", status="ARCHIVED")
        with pytest.raises(ServiceError) as exc_arch:
            update_project_facets(
                db_session,
                project_key="prj-lp-arch",
                provided={"log_profile": {"aee_mode": "3"}},
                **actor,
            )
        assert exc_arch.value.status == 409

    def test_direct_service_call_rejects_invalid_log_profile_with_422(self, db_session, actor):
        _make_project(db_session, "prj-lp-bad")
        with pytest.raises(ServiceError) as exc:
            update_project_facets(
                db_session,
                project_key="prj-lp-bad",
                provided={"log_profile": {"mobilelog_size_mb": 128}},
                **actor,
            )
        assert exc.value.status == 422
