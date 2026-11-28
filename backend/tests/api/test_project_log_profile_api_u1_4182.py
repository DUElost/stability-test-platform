"""ADR-0067 U1 — /api/v1/projects/{project_key} log_profile HTTP API 测试（#4182）。"""

from __future__ import annotations

import pytest

from backend.models.audit import AuditLog
from backend.models.project import TestProject


@pytest.fixture
def user_project(db_session):
    p = TestProject(
        project_key="honor-mld-u1",
        display_name="Honor MLD U1",
        customer="荣耀",
        source="USER",
        status="ACTIVE",
    )
    db_session.add(p)
    db_session.commit()
    return p


class TestProjectLogProfileAPI:
    def test_admin_sets_reads_and_clears_log_profile(
        self, client, admin_headers, auth_headers, user_project, db_session,
    ):
        # 初始读取：所有登录用户可读，默认 log_profile=null, version=0
        r_init = client.get(f"/api/v1/projects/{user_project.project_key}", headers=auth_headers)
        assert r_init.status_code == 200
        body = r_init.json()["data"]
        assert body["log_profile"] is None
        assert body["log_profile_version"] == 0

        # 管理员写入合法 7 键子集
        r_put = client.put(
            f"/api/v1/projects/{user_project.project_key}",
            headers=admin_headers,
            json={
                "log_profile": {
                    "mobilelog_size_mb": 2048,
                    "aee_mode": "3",
                    "ylog_size_mb": 4096,
                    "ylog_required_streams": ["android", "kernel", "uboot"],
                },
            },
        )
        assert r_put.status_code == 200
        updated = r_put.json()["data"]
        assert updated["log_profile"] == {
            "mobilelog_size_mb": 2048,
            "aee_mode": "3",
            "ylog_size_mb": 4096,
            "ylog_required_streams": ["android", "kernel", "uboot"],
        }
        assert updated["log_profile_version"] == 1

        # 列表页与详情页对普通用户均可见
        r_list = client.get("/api/v1/projects", headers=auth_headers)
        assert r_list.status_code == 200
        items = {item["project_key"]: item for item in r_list.json()["data"]}
        assert items[user_project.project_key]["log_profile_version"] == 1
        assert items[user_project.project_key]["log_profile"]["aee_mode"] == "3"

        # 仅改 display_name 不影响 log_profile 及版本号
        r_other = client.put(
            f"/api/v1/projects/{user_project.project_key}",
            headers=admin_headers,
            json={"display_name": "Honor MLD Renamed"},
        )
        assert r_other.status_code == 200
        assert r_other.json()["data"]["log_profile_version"] == 1
        assert r_other.json()["data"]["log_profile"]["aee_mode"] == "3"

        # 传 null 清空并递增版本号
        r_clear = client.put(
            f"/api/v1/projects/{user_project.project_key}",
            headers=admin_headers,
            json={"log_profile": None},
        )
        assert r_clear.status_code == 200
        assert r_clear.json()["data"]["log_profile"] is None
        assert r_clear.json()["data"]["log_profile_version"] == 2

        audits = (
            db_session.query(AuditLog)
            .filter(
                AuditLog.resource_id == str(user_project.id),
                AuditLog.action == "update_project",
            )
            .all()
        )
        lp_audits = [a for a in audits if (a.details or {}).get("field") == "log_profile"]
        assert len(lp_audits) == 2
        assert lp_audits[0].details["new_version"] == 1
        assert lp_audits[1].details["new_version"] == 2

    def test_non_admin_cannot_write_log_profile(self, client, auth_headers, user_project):
        resp = client.put(
            f"/api/v1/projects/{user_project.project_key}",
            headers=auth_headers,
            json={"log_profile": {"aee_mode": "3"}},
        )
        assert resp.status_code == 403

    @pytest.mark.parametrize(
        "bad_payload",
        [
            {"timezone": "Asia/Shanghai"},
            {"sysdump_enabled": True},
            {"mobilelog_size_mb": 64},
            {"mobilelog_size_mb": 255},
            {"mobilelog_size_mb": 20481},
            {"ylog_size_mb": 63},
            {"ylog_size_mb": 10241},
            {"ylog_required_streams": ["sysinfo"]},
            {"ylog_required_streams": ["android"]},
            {"ylog_required_streams": ["android", "kernel", "sysinfo"]},
            {"aee_mode": "bad"},
            {"mtk_sublog_cmd": "bad;cmd"},
        ],
    )
    def test_rejects_invalid_log_profile_with_422(
        self, client, admin_headers, user_project, bad_payload,
    ):
        resp = client.put(
            f"/api/v1/projects/{user_project.project_key}",
            headers=admin_headers,
            json={"log_profile": bad_payload},
        )
        assert resp.status_code == 422

    def test_seed_422_and_archived_409(self, client, admin_headers, db_session):
        seed = TestProject(
            project_key="HONOR-MLD", display_name="Seed", source="SEED", status="ACTIVE",
        )
        archived = TestProject(
            project_key="proj-arch-u1", display_name="Archived", source="USER", status="ARCHIVED",
        )
        db_session.add_all([seed, archived])
        db_session.commit()

        r_seed = client.put(
            "/api/v1/projects/HONOR-MLD",
            headers=admin_headers,
            json={"log_profile": {"aee_mode": "3"}},
        )
        assert r_seed.status_code == 422

        r_arch = client.put(
            "/api/v1/projects/proj-arch-u1",
            headers=admin_headers,
            json={"log_profile": {"aee_mode": "3"}},
        )
        assert r_arch.status_code == 409
