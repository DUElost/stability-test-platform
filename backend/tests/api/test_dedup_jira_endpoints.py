from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from backend.models.jira_run import JiraRun
from backend.services.run_console import RunConsole, RunConsoleError, RunKeyBusyError


@pytest.fixture(autouse=True)
def reset_run_console_singleton():
    RunConsole._reset_for_tests()
    yield
    RunConsole._reset_for_tests()


@pytest.fixture
def mock_run_console(monkeypatch):
    """Mock RunConsole.instance() 返回 MagicMock，避免起真子进程。"""
    inst = MagicMock()
    inst.start.return_value = "con-fake-123"
    inst.status.return_value = {
        "run_id": "con-fake-123",
        "run_key": "jira:transsion",
        "label": "test",
        "status": "RUNNING",
        "exit_code": None,
        "started_at": "2026-06-18T00:00:00Z",
        "ended_at": None,
        "seq": 0,
        "error": None,
    }
    inst.read_log.return_value = {
        "run_id": "con-fake-123",
        "from_seq": 1,
        "lines": ["line1", "line2"],
        "seq": 2,
        "status": "RUNNING",
    }
    inst.cancel.return_value = True
    monkeypatch.setattr("backend.api.routes.dedup.RunConsole.instance", lambda: inst)
    return inst


def _set_vendor_env(monkeypatch, vendor="transsion"):
    monkeypatch.setenv(f"STP_JIRA_{vendor.upper()}_PYTHON", "/opt/fake/python")
    monkeypatch.setenv(f"STP_JIRA_{vendor.upper()}_DIR", "/opt/fake/tool")


class TestStartJiraRun:
    """POST /api/v1/jira/runs"""

    def test_unauthenticated_returns_401(self, client):
        resp = client.post("/api/v1/jira/runs")
        assert resp.status_code == 401

    def test_invalid_vendor_returns_422(self, client, auth_headers):
        resp = client.post(
            "/api/v1/jira/runs",
            data={"vendor": "moto", "stage": "create"},
            headers=auth_headers,
        )
        assert resp.status_code == 422

    def test_invalid_stage_returns_422(self, client, auth_headers, monkeypatch):
        _set_vendor_env(monkeypatch)
        resp = client.post(
            "/api/v1/jira/runs",
            data={"vendor": "transsion", "stage": "bogus"},
            files={"file": ("x.xls", b"data", "application/vnd.ms-excel")},
            headers=auth_headers,
        )
        assert resp.status_code == 422

    def test_vendor_env_unset_returns_503(self, client, auth_headers, monkeypatch):
        monkeypatch.delenv("STP_JIRA_TRANSSION_PYTHON", raising=False)
        monkeypatch.delenv("STP_JIRA_TRANSSION_DIR", raising=False)
        resp = client.post(
            "/api/v1/jira/runs",
            data={"vendor": "transsion", "stage": "create"},
            files={"file": ("x.xls", b"data", "application/vnd.ms-excel")},
            headers=auth_headers,
        )
        assert resp.status_code == 503
        assert "not configured" in resp.json()["detail"].lower()

    def test_run_key_busy_returns_409(
        self, client, auth_headers, monkeypatch, mock_run_console
    ):
        _set_vendor_env(monkeypatch)
        mock_run_console.start.side_effect = RunKeyBusyError("busy")
        resp = client.post(
            "/api/v1/jira/runs",
            data={"vendor": "transsion", "stage": "create", "dry_run": "true"},
            files={"file": ("x.xls", b"data", "application/vnd.ms-excel")},
            headers=auth_headers,
        )
        assert resp.status_code == 409
        assert "in progress" in resp.json()["detail"].lower()

    def test_spawn_failure_returns_500(
        self, client, auth_headers, monkeypatch, mock_run_console
    ):
        _set_vendor_env(monkeypatch)
        mock_run_console.start.side_effect = RunConsoleError("spawn failed")
        resp = client.post(
            "/api/v1/jira/runs",
            data={"vendor": "transsion", "stage": "create", "dry_run": "true"},
            files={"file": ("x.xls", b"data", "application/vnd.ms-excel")},
            headers=auth_headers,
        )
        assert resp.status_code == 500

    def test_upload_list_success(
        self, client, auth_headers, monkeypatch, mock_run_console
    ):
        _set_vendor_env(monkeypatch)
        resp = client.post(
            "/api/v1/jira/runs",
            data={"vendor": "transsion", "stage": "upload_list", "dry_run": "true"},
            files={"file": ("Result.xls", b"fake-xls", "application/vnd.ms-excel")},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["console_run_id"] == "con-fake-123"
        assert data["room"] == "console:con-fake-123"
        assert data["vendor"] == "transsion"
        assert data["stage"] == "upload_list"
        mock_run_console.start.assert_called_once()

    def test_create_stage_success_with_dry_run_flag(
        self, client, auth_headers, monkeypatch, mock_run_console
    ):
        _set_vendor_env(monkeypatch, vendor="tinno")
        resp = client.post(
            "/api/v1/jira/runs",
            data={"vendor": "tinno", "stage": "create", "dry_run": "true"},
            files={"file": ("JIRA_Upload_List.xlsx", b"data", "application/vnd.ms-excel")},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        argv = mock_run_console.start.call_args.kwargs.get("cmd", [])
        assert any("create_tinno_jira_batch_from_excel.py" in str(a) for a in argv)
        assert "--dry-run" in argv

    def test_upload_filename_traversal_is_sanitized(
        self, client, auth_headers, monkeypatch, mock_run_console
    ):
        _set_vendor_env(monkeypatch)
        resp = client.post(
            "/api/v1/jira/runs",
            data={"vendor": "transsion", "stage": "upload_list", "dry_run": "true"},
            files={"file": ("../../evil.xls", b"fake-xls", "application/vnd.ms-excel")},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        argv = mock_run_console.start.call_args.kwargs.get("cmd", [])
        assert not any(".." in str(a) for a in argv)
        assert any(str(a).endswith(".xls") and "evil" not in str(a) for a in argv)

    def test_upload_rejects_unsupported_extension(
        self, client, auth_headers, monkeypatch
    ):
        _set_vendor_env(monkeypatch)
        resp = client.post(
            "/api/v1/jira/runs",
            data={"vendor": "transsion", "stage": "upload_list", "dry_run": "true"},
            files={"file": ("list.txt", b"fake", "text/plain")},
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert "file must be .xls/.xlsx" in resp.json()["detail"]


class TestGetJiraRunStatus:
    """GET /api/v1/jira/runs/{console_run_id}"""

    def test_unauthenticated_returns_401(self, client):
        resp = client.get("/api/v1/jira/runs/con-x")
        assert resp.status_code == 401

    def test_unknown_run_returns_404(self, client, auth_headers, monkeypatch):
        inst = MagicMock()
        inst.status.return_value = None
        monkeypatch.setattr("backend.api.routes.dedup.RunConsole.instance", lambda: inst)
        resp = client.get("/api/v1/jira/runs/con-missing", headers=auth_headers)
        assert resp.status_code == 404

    def test_existing_run_returns_200(self, client, auth_headers, mock_run_console):
        resp = client.get("/api/v1/jira/runs/con-fake-123", headers=auth_headers)
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["run_id"] == "con-fake-123"
        assert data["status"] == "RUNNING"


class TestGetJiraRunLog:
    """GET /api/v1/jira/runs/{console_run_id}/log"""

    def test_unauthenticated_returns_401(self, client):
        resp = client.get("/api/v1/jira/runs/con-x/log")
        assert resp.status_code == 401

    def test_unknown_run_returns_404(self, client, auth_headers, monkeypatch):
        inst = MagicMock()
        inst.status.return_value = None
        inst.log_file_path.return_value.exists.return_value = False
        monkeypatch.setattr("backend.api.routes.dedup.RunConsole.instance", lambda: inst)
        resp = client.get("/api/v1/jira/runs/con-missing/log", headers=auth_headers)
        assert resp.status_code == 404

    def test_log_replay_from_file_when_not_in_memory(self, client, auth_headers, monkeypatch):
        inst = MagicMock()
        inst.status.return_value = None
        inst.log_file_path.return_value.exists.return_value = True
        inst.read_log.return_value = {
            "run_id": "con-historic",
            "from_seq": 1,
            "lines": ["archived line"],
            "seq": 1,
            "status": "UNKNOWN",
        }
        monkeypatch.setattr("backend.api.routes.dedup.RunConsole.instance", lambda: inst)
        resp = client.get("/api/v1/jira/runs/con-historic/log", headers=auth_headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["lines"] == ["archived line"]
        inst.read_log.assert_called_once_with("con-historic", from_seq=0)

    def test_log_replay_returns_200(self, client, auth_headers, mock_run_console):
        resp = client.get(
            "/api/v1/jira/runs/con-fake-123/log?from_seq=1", headers=auth_headers
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["lines"] == ["line1", "line2"]
        assert data["seq"] == 2

    def test_negative_from_seq_returns_422(self, client, auth_headers, mock_run_console):
        resp = client.get(
            "/api/v1/jira/runs/con-fake-123/log?from_seq=-1", headers=auth_headers
        )
        assert resp.status_code == 422


class TestCancelJiraRun:
    """POST /api/v1/jira/runs/{console_run_id}/cancel"""

    def test_unauthenticated_returns_401(self, client):
        resp = client.post("/api/v1/jira/runs/con-x/cancel")
        assert resp.status_code == 401

    def test_unknown_run_returns_404(self, client, auth_headers, monkeypatch):
        inst = MagicMock()
        inst.status.return_value = None
        monkeypatch.setattr("backend.api.routes.dedup.RunConsole.instance", lambda: inst)
        resp = client.post("/api/v1/jira/runs/con-missing/cancel", headers=auth_headers)
        assert resp.status_code == 404

    def test_cancel_returns_200(self, client, auth_headers, mock_run_console):
        resp = client.post("/api/v1/jira/runs/con-fake-123/cancel", headers=auth_headers)
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["console_run_id"] == "con-fake-123"
        assert data["canceled"] is True


class TestJiraProjectKeyWiring:
    """G17：source=plan_run 解析登记簿 jira_project_key 并注入 stage1 argv。"""

    @staticmethod
    def _seed_plan_run(db_session, *, jira_project_key=None):
        """TestProject → Plan → PlanRun → PlanRunArtifact 四层种子，返回 artifact id。"""
        from backend.models.plan import Plan
        from backend.models.plan_run import PlanRun
        from backend.models.plan_run_artifact import PlanRunArtifact
        from backend.models.project import TestProject

        project = TestProject(
            project_key="pz-test",
            display_name="G17 seed",
            jira_project_key=jira_project_key,
        )
        db_session.add(project)
        db_session.flush()
        plan = Plan(name="g17-plan", failure_threshold=0.1, created_by="t", project_id=project.id)
        db_session.add(plan)
        db_session.flush()
        run = PlanRun(
            plan_id=plan.id,
            status="RUNNING",
            failure_threshold=plan.failure_threshold,
            plan_snapshot={"name": plan.name},
            run_type="MANUAL",
            triggered_by="test",
        )
        db_session.add(run)
        db_session.flush()
        artifact = PlanRunArtifact(plan_run_id=run.id, storage_uri="/mnt/fake/Result_org.xls")
        db_session.add(artifact)
        db_session.commit()
        return artifact.id, run.id

    def _post_plan_run_source(self, client, auth_headers, artifact_id, stage="upload_list"):
        return client.post(
            "/api/v1/jira/runs",
            data={
                "vendor": "transsion",
                "stage": stage,
                "dry_run": "true",
                "source": "plan_run",
                "artifact_id": str(artifact_id),
            },
            headers=auth_headers,
        )

    def test_resolved_key_injected_into_upload_list_argv(
        self, client, auth_headers, monkeypatch, mock_run_console, db_session
    ):
        artifact_id, _ = self._seed_plan_run(db_session, jira_project_key="ZKEY")
        _set_vendor_env(monkeypatch)
        resp = self._post_plan_run_source(client, auth_headers, artifact_id)
        assert resp.status_code == 200, resp.text
        argv = mock_run_console.start.call_args.kwargs.get("cmd", [])
        assert "--set-project-key" in argv
        assert argv[argv.index("--set-project-key") + 1] == "ZKEY"
        assert resp.json()["data"]["jira_project_key"] == "ZKEY"

    def test_missing_key_soft_falls_back_without_flag(
        self, client, auth_headers, monkeypatch, mock_run_console, db_session
    ):
        artifact_id, _ = self._seed_plan_run(db_session, jira_project_key=None)
        _set_vendor_env(monkeypatch)
        resp = self._post_plan_run_source(client, auth_headers, artifact_id)
        # 缺键不阻断：argv 不带 --set-project-key（工具用自身 config 映射），响应透出 null
        assert resp.status_code == 200, resp.text
        argv = mock_run_console.start.call_args.kwargs.get("cmd", [])
        assert "--set-project-key" not in argv
        assert resp.json()["data"]["jira_project_key"] is None

    def test_create_stage_never_injects_flag(
        self, client, auth_headers, monkeypatch, mock_run_console, db_session
    ):
        artifact_id, _ = self._seed_plan_run(db_session, jira_project_key="ZKEY")
        _set_vendor_env(monkeypatch)
        resp = self._post_plan_run_source(client, auth_headers, artifact_id, stage="create")
        assert resp.status_code == 200, resp.text
        argv = mock_run_console.start.call_args.kwargs.get("cmd", [])
        # 注入只属 stage1（create 消费的模板已带 Project 列）……
        assert "--set-project-key" not in argv
        # ……但解析与审计照常：响应/落库仍透出登记簿键
        assert resp.json()["data"]["jira_project_key"] == "ZKEY"

    def test_persisted_row_records_key(
        self, client, auth_headers, monkeypatch, mock_run_console, db_session
    ):
        artifact_id, _ = self._seed_plan_run(db_session, jira_project_key="AUDIT1")
        _set_vendor_env(monkeypatch)
        resp = self._post_plan_run_source(client, auth_headers, artifact_id)
        console_run_id = resp.json()["data"]["console_run_id"]
        row = (
            db_session.query(JiraRun)
            .filter_by(console_run_id=console_run_id)
            .one_or_none()
        )
        # 落库走独立 SessionLocal；本断言在真 PG 下可见，SQLite 变体亦同步提交
        assert row is not None
        assert row.jira_project_key == "AUDIT1"
