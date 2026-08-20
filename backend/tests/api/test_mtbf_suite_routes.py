"""ADR-0030 P1a — MTBF 套件/用例管理面 API。

覆盖：
- 套件 CRUD（创建 / 列表 / 详情 / 更新 / 软删；name 冲突 409、未知项目 404）
- 用例 CRUD（创建 / 列表 / 整覆盖更新 / 删除；suite 内重名 409）
- import（130 条真实快照入库 + 整体替换语义 + source_sha256）
- export（渲染字节与生产文件**逐字节一致**）/ global / validate
- export-to-tool-dir（atomic write + 两个漂移基线 + 审计）
- **结构性漂移检测**：改任意内容后 export_stale 自动翻转，端点不清快照列
- 非 admin 403
"""
from __future__ import annotations

from pathlib import Path

import pytest

from backend.models.audit import AuditLog
from backend.models.project import TestProject as ProjectModel
from backend.models.test_suite import TestCase, TestSuite

_FIXTURES = Path(__file__).resolve().parents[2] / "agent" / "tests" / "fixtures" / "mtbf"


@pytest.fixture
def real_runtask() -> bytes:
    return (_FIXTURES / "runtask.xml").read_bytes()


@pytest.fixture
def real_global() -> bytes:
    return (_FIXTURES / "ui_automator_test_data.xml").read_bytes()


@pytest.fixture
def suite(db_session) -> TestSuite:
    s = TestSuite(name="MTBF-legacy", display_name="通用 MTBF", root_config={})
    db_session.add(s)
    db_session.commit()
    return s


def _add_case(db_session, suite, name, ordinal=1, enabled=True, times=1):
    case = TestCase(
        suite_id=suite.id, name=name, ordinal=ordinal, enabled=enabled, times=times,
        exec_descs=[{"class": "C", "method": "m", "args": {}}],
    )
    db_session.add(case)
    db_session.commit()
    return case


class TestSuiteCrud:
    def test_create_list_detail(self, client, admin_headers, auth_headers):
        resp = client.post("/api/v1/test-suites", headers=admin_headers,
                           json={"name": "S1", "display_name": "套件一"})
        assert resp.status_code == 200
        suite_id = resp.json()["data"]["id"]

        listed = client.get("/api/v1/test-suites", headers=auth_headers).json()["data"]
        assert [s["name"] for s in listed] == ["S1"]

        detail = client.get(f"/api/v1/test-suites/{suite_id}", headers=auth_headers).json()["data"]
        assert detail["export_dir"] == "legacy"        # 无项目 → legacy（兼容 P0 部署）
        assert detail["export_stale"] is True          # 从未导出即 stale

    def test_duplicate_name_409(self, client, admin_headers, suite):
        resp = client.post("/api/v1/test-suites", headers=admin_headers,
                           json={"name": suite.name})
        assert resp.status_code == 409

    def test_unknown_project_key_404(self, client, admin_headers):
        resp = client.post("/api/v1/test-suites", headers=admin_headers,
                           json={"name": "S2", "project_key": "nope"})
        assert resp.status_code == 404

    def test_export_dir_falls_back_to_project_key(self, client, auth_headers,
                                                  admin_headers, db_session):
        db_session.add(ProjectModel(project_key="cam", display_name="Camera"))
        db_session.commit()
        sid = client.post("/api/v1/test-suites", headers=admin_headers,
                          json={"name": "S3", "project_key": "cam"}).json()["data"]["id"]
        detail = client.get(f"/api/v1/test-suites/{sid}", headers=auth_headers).json()["data"]
        assert detail["export_dir"] == "cam"

    def test_soft_delete(self, client, admin_headers, suite):
        assert client.delete(f"/api/v1/test-suites/{suite.id}",
                             headers=admin_headers).status_code == 200
        active = client.get("/api/v1/test-suites?is_active=true",
                            headers=admin_headers).json()["data"]
        assert active == []

    def test_non_admin_forbidden(self, client, auth_headers, suite):
        assert client.post("/api/v1/test-suites", headers=auth_headers,
                           json={"name": "X"}).status_code == 403
        assert client.put(f"/api/v1/test-suites/{suite.id}", headers=auth_headers,
                          json={"display_name": "x"}).status_code == 403
        assert client.delete(f"/api/v1/test-suites/{suite.id}",
                             headers=auth_headers).status_code == 403


class TestCaseCrud:
    def test_create_update_delete(self, client, admin_headers, auth_headers, suite):
        created = client.post(
            f"/api/v1/test-suites/{suite.id}/cases", headers=admin_headers,
            json={"name": "c1", "ordinal": 1,
                  "exec_descs": [{"class": "C", "method": "m"}]},
        )
        assert created.status_code == 200
        case_id = created.json()["data"]["id"]

        updated = client.put(f"/api/v1/test-cases/{case_id}", headers=admin_headers,
                             json={"name": "c1", "ordinal": 1, "times": 9,
                                   "enabled": False, "exec_descs": []})
        assert updated.json()["data"]["times"] == 9
        assert updated.json()["data"]["enabled"] is False

        cases = client.get(f"/api/v1/test-suites/{suite.id}/cases",
                           headers=auth_headers).json()["data"]
        assert len(cases) == 1

        assert client.delete(f"/api/v1/test-cases/{case_id}",
                             headers=admin_headers).status_code == 200
        assert client.get(f"/api/v1/test-suites/{suite.id}/cases",
                          headers=auth_headers).json()["data"] == []

    def test_duplicate_case_name_409(self, client, admin_headers, suite, db_session):
        _add_case(db_session, suite, "dup")
        resp = client.post(f"/api/v1/test-suites/{suite.id}/cases",
                           headers=admin_headers, json={"name": "dup"})
        assert resp.status_code == 409


class TestImportExport:
    def test_import_real_snapshot(self, client, admin_headers, suite,
                                  real_runtask, real_global):
        resp = client.post(
            f"/api/v1/test-suites/{suite.id}/import", headers=admin_headers,
            files={"file": ("runtask.xml", real_runtask, "application/xml"),
                   "global": ("UiAutomatorTestData.xml", real_global, "application/xml")},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["case_count"] == 130
        assert data["root_config"]["times"] == "1000"
        assert data["global_params"]["sim"]["wifiName"] == "example-wifi"
        # TestSet 根属性必须带进库，否则导出物丢 TakeScreenshot
        assert data["global_params"]["test_set_attrs"]["TakeScreenshot"] == "true"
        assert data["source_sha256"]

    def test_export_is_byte_identical_to_source(self, client, admin_headers,
                                                auth_headers, suite, real_runtask):
        """导入 → 导出必须逐字节回到生产文件：设备面输入不能因为过了一次库就变形。"""
        client.post(f"/api/v1/test-suites/{suite.id}/import", headers=admin_headers,
                    files={"file": ("runtask.xml", real_runtask, "application/xml")})
        exported = client.get(f"/api/v1/test-suites/{suite.id}/export",
                              headers=auth_headers)
        assert exported.status_code == 200
        assert exported.content == real_runtask

    def test_import_replaces_removed_cases(self, client, admin_headers, suite,
                                           db_session, real_runtask):
        _add_case(db_session, suite, "stale-case", ordinal=999)
        client.post(f"/api/v1/test-suites/{suite.id}/import", headers=admin_headers,
                    files={"file": ("runtask.xml", real_runtask, "application/xml")})
        names = {c.name for c in db_session.query(TestCase).all()}
        assert "stale-case" not in names       # 上一版残留不得悄悄进导出物

    def test_times_override(self, client, admin_headers, auth_headers, suite,
                            real_runtask):
        client.post(f"/api/v1/test-suites/{suite.id}/import", headers=admin_headers,
                    files={"file": ("runtask.xml", real_runtask, "application/xml")})
        out = client.get(f"/api/v1/test-suites/{suite.id}/export?times=55",
                         headers=auth_headers).content
        assert b'times="55"' in out

    def test_validate_reports_library_issues(self, client, admin_headers,
                                             auth_headers, suite, db_session):
        _add_case(db_session, suite, "bad")
        db_session.query(TestCase).filter(TestCase.name == "bad").update(
            {"exec_descs": [{"class": "", "method": ""}]}
        )
        db_session.commit()
        data = client.post(f"/api/v1/test-suites/{suite.id}/validate",
                           headers=auth_headers).json()["data"]
        assert data["valid"] is False
        assert {i["code"] for i in data["issues"]} >= {
            "TESTCASE_MISSING_CLASS", "TESTCASE_MISSING_METHOD"
        }


class TestExportToToolDirAndDriftDetection:
    @pytest.fixture(autouse=True)
    def _storage_root(self, tmp_path, monkeypatch):
        monkeypatch.setenv("STP_AEE_NFS_ROOT", str(tmp_path))
        return tmp_path

    def _export(self, client, admin_headers, suite_id):
        return client.post(f"/api/v1/test-suites/{suite_id}/export-to-tool-dir",
                           headers=admin_headers)

    def test_writes_both_files_and_records_baselines(self, client, admin_headers,
                                                     auth_headers, suite, db_session,
                                                     real_runtask, _storage_root):
        client.post(f"/api/v1/test-suites/{suite.id}/import", headers=admin_headers,
                    files={"file": ("runtask.xml", real_runtask, "application/xml")})
        resp = self._export(client, admin_headers, suite.id)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["export_dir"] == "legacy"

        written = Path(data["runtask_path"]).read_bytes()
        assert written == real_runtask          # 落盘的就是设备面既有输入
        assert Path(data["global_path"]).is_file()

        detail = client.get(f"/api/v1/test-suites/{suite.id}",
                            headers=auth_headers).json()["data"]
        assert detail["export_stale"] is False
        assert detail["exported_content_sha256"] == detail["content_sha256"]

    @pytest.mark.parametrize("mutate", ["add", "delete", "rename", "times",
                                        "disable", "root_config"])
    def test_any_library_change_flips_stale_without_endpoint_nulling(
        self, client, admin_headers, auth_headers, suite, db_session,
        real_runtask, mutate,
    ):
        """核心不变量：六条变更路径都让 export_stale 翻真——**没有任何端点清过快照列**。

        这正是「结构性检测 vs 端点置空纪律」的差别：置空写法下每新增一条写路径
        都要记得清列，漏一条就是「库改了门禁放行」。
        """
        client.post(f"/api/v1/test-suites/{suite.id}/import", headers=admin_headers,
                    files={"file": ("runtask.xml", real_runtask, "application/xml")})
        self._export(client, admin_headers, suite.id)
        assert client.get(f"/api/v1/test-suites/{suite.id}",
                          headers=auth_headers).json()["data"]["export_stale"] is False

        first = db_session.query(TestCase).filter(
            TestCase.suite_id == suite.id).order_by(TestCase.ordinal).first()
        if mutate == "add":
            client.post(f"/api/v1/test-suites/{suite.id}/cases", headers=admin_headers,
                        json={"name": "brand-new", "ordinal": 999})
        elif mutate == "delete":
            client.delete(f"/api/v1/test-cases/{first.id}", headers=admin_headers)
        elif mutate == "rename":
            client.put(f"/api/v1/test-cases/{first.id}", headers=admin_headers,
                       json={"name": "renamed", "ordinal": first.ordinal,
                             "times": first.times, "enabled": True,
                             "exec_descs": first.exec_descs})
        elif mutate == "times":
            client.put(f"/api/v1/test-cases/{first.id}", headers=admin_headers,
                       json={"name": first.name, "ordinal": first.ordinal,
                             "times": 42, "enabled": True,
                             "exec_descs": first.exec_descs})
        elif mutate == "disable":
            client.put(f"/api/v1/test-cases/{first.id}", headers=admin_headers,
                       json={"name": first.name, "ordinal": first.ordinal,
                             "times": first.times, "enabled": False,
                             "exec_descs": first.exec_descs})
        elif mutate == "root_config":
            client.put(f"/api/v1/test-suites/{suite.id}", headers=admin_headers,
                       json={"root_config": {"times": "2000"}})

        detail = client.get(f"/api/v1/test-suites/{suite.id}",
                            headers=auth_headers).json()["data"]
        assert detail["export_stale"] is True, f"{mutate} 未翻转 stale"

    def test_reexport_clears_stale(self, client, admin_headers, auth_headers,
                                   suite, real_runtask):
        client.post(f"/api/v1/test-suites/{suite.id}/import", headers=admin_headers,
                    files={"file": ("runtask.xml", real_runtask, "application/xml")})
        self._export(client, admin_headers, suite.id)
        client.put(f"/api/v1/test-suites/{suite.id}", headers=admin_headers,
                   json={"root_config": {"times": "2000"}})
        assert client.get(f"/api/v1/test-suites/{suite.id}",
                          headers=auth_headers).json()["data"]["export_stale"] is True
        self._export(client, admin_headers, suite.id)
        assert client.get(f"/api/v1/test-suites/{suite.id}",
                          headers=auth_headers).json()["data"]["export_stale"] is False

    def test_export_header_signals_stale(self, client, admin_headers, auth_headers,
                                         suite, real_runtask):
        client.post(f"/api/v1/test-suites/{suite.id}/import", headers=admin_headers,
                    files={"file": ("runtask.xml", real_runtask, "application/xml")})
        assert client.get(f"/api/v1/test-suites/{suite.id}/export",
                          headers=auth_headers).headers["X-Export-Stale"] == "1"
        self._export(client, admin_headers, suite.id)
        assert client.get(f"/api/v1/test-suites/{suite.id}/export",
                          headers=auth_headers).headers["X-Export-Stale"] == "0"

    def test_non_admin_cannot_export(self, client, auth_headers, suite):
        assert self._export(client, auth_headers, suite.id).status_code == 403


class TestAudit:
    def test_all_writes_recorded(self, client, admin_headers, db_session,
                                 suite, real_runtask, tmp_path, monkeypatch):
        monkeypatch.setenv("STP_AEE_NFS_ROOT", str(tmp_path))
        client.post(f"/api/v1/test-suites/{suite.id}/import", headers=admin_headers,
                    files={"file": ("runtask.xml", real_runtask, "application/xml")})
        client.post(f"/api/v1/test-suites/{suite.id}/export-to-tool-dir",
                    headers=admin_headers)
        client.put(f"/api/v1/test-suites/{suite.id}", headers=admin_headers,
                   json={"display_name": "renamed"})
        actions = {
            a.action for a in db_session.query(AuditLog)
            .filter(AuditLog.resource_type == "test_suite").all()
        }
        assert {"import", "export", "update"} <= actions


class TestArgOrderSurvivesDbRoundTrip:
    """`args` 键序必须逐字保留——这条断言钉的是列类型选择，不是渲染逻辑。

    PG 的 JSONB 会按「长度优先再字节序」重排对象键：`wifiPWD`(7) 会排到
    `wifiName`(8) 前面，导出物随即不再与设备面输入同构。因此 root_config /
    global_params / exec_descs 三列用 JSON。若将来有人为了索引改回 JSONB，
    本测试先于 golden 挂掉并直接指出原因。
    """

    def test_arg_key_order_preserved(self, client, admin_headers, auth_headers,
                                     suite, real_runtask, db_session):
        client.post(f"/api/v1/test-suites/{suite.id}/import", headers=admin_headers,
                    files={"file": ("runtask.xml", real_runtask, "application/xml")})
        row = (
            db_session.query(TestCase)
            .filter(TestCase.suite_id == suite.id)
            .order_by(TestCase.ordinal)
            .first()
        )
        assert list(row.exec_descs[0]["args"].keys()) == ["wifiName", "wifiPWD"]

        exported = client.get(f"/api/v1/test-suites/{suite.id}/export",
                              headers=auth_headers).content
        first_arg = exported.index(b"<arg ")
        assert exported[first_arg:first_arg + 60].startswith(b'<arg name="wifiName"')
