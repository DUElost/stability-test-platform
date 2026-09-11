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
    """Mock RunConsole.instance() 返回 MagicMock，避免起真子进程。

    #1084 起 console_run_id 由路由预生成并传入 start(run_id=...) —— fake 直接
    回显该值，保证响应/落库/断言三方一致。
    """
    inst = MagicMock()

    def _fake_start(*args, **kwargs):
        return kwargs.get("run_id") or "con-fake-123"

    inst.start.side_effect = _fake_start
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
        self, client, auth_headers, monkeypatch, mock_run_console, db_session
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
        # #1084：console_run_id 由路由预生成（先落库后启动），不再等于 fake 固定值
        assert data["console_run_id"].startswith("con-")
        assert data["room"] == f"console:{data['console_run_id']}"
        # 行已在 start 之前落库（RUNNING）
        row = db_session.query(JiraRun).filter_by(
            console_run_id=data["console_run_id"],
        ).one()
        assert row.status == "RUNNING"
        assert mock_run_console.start.call_args.kwargs.get("run_id") == data["console_run_id"]
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
        # 单实例下不应附加多实例诊断（#1114）
        assert "#1114" not in resp.json()["detail"]

    def test_unknown_run_404_includes_multi_instance_hint(self, client, auth_headers, monkeypatch):
        """#1114：多实例下 404 带 owner-less 诊断——本地缺失可能由其他实例持有。"""
        inst = MagicMock()
        inst.status.return_value = None
        monkeypatch.setattr("backend.api.routes.dedup.RunConsole.instance", lambda: inst)
        monkeypatch.setattr(
            "backend.realtime.socketio_redis.socketio_redis_adapter_enabled", lambda: True
        )
        resp = client.get("/api/v1/jira/runs/con-missing", headers=auth_headers)
        assert resp.status_code == 404
        assert "#1114" in resp.json()["detail"]

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

    def test_unknown_run_returns_404(self, client, auth_headers, monkeypatch, db_session):
        inst = MagicMock()
        inst.status.return_value = None
        monkeypatch.setattr("backend.api.routes.dedup.RunConsole.instance", lambda: inst)
        resp = client.post("/api/v1/jira/runs/con-missing/cancel", headers=auth_headers)
        assert resp.status_code == 404
        # R02-F07（#907）：404（探测/误操作）同样可归责
        self._assert_audit(db_session, resource_id="con-missing", reason="run_not_found")

    def test_cancel_returns_200(self, client, auth_headers, mock_run_console, db_session):
        resp = client.post("/api/v1/jira/runs/con-fake-123/cancel", headers=auth_headers)
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["console_run_id"] == "con-fake-123"
        assert data["canceled"] is True
        self._assert_audit(db_session, resource_id="con-fake-123", canceled=True)

    @staticmethod
    def _assert_audit(db_session, *, resource_id, reason=None, canceled=None):
        from backend.models.audit import AuditLog

        latest = (
            db_session.query(AuditLog)
            .filter(AuditLog.action == "jira_run_cancel")
            .filter(AuditLog.resource_id == resource_id)
            .order_by(AuditLog.id.desc())
            .first()
        )
        assert latest is not None, "jira_run_cancel 审计行缺失"
        assert latest.username == "testuser"
        if reason is not None:
            assert latest.details["reason"] == reason
        if canceled is not None:
            assert latest.details["canceled"] is canceled


class TestReloadAgentConfig:
    """POST /api/v1/plan-runs/hosts/{host_id}/reload-config"""

    def test_unauthenticated_returns_401(self, client):
        resp = client.post("/api/v1/plan-runs/hosts/h1/reload-config")
        assert resp.status_code == 401

    def test_reload_success_audited(self, client, auth_headers, db_session, monkeypatch):
        from unittest.mock import AsyncMock, patch

        with patch(
            "backend.realtime.socketio_server.emit_agent_control",
            new=AsyncMock(return_value=None),
        ):
            resp = client.post(
                "/api/v1/plan-runs/hosts/h1/reload-config", headers=auth_headers
            )
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["status"] == "sent"

        from backend.models.audit import AuditLog

        rows = (
            db_session.query(AuditLog)
            .filter(AuditLog.action == "agent_config_reload")
            .all()
        )
        assert rows, "agent_config_reload 审计行缺失"
        latest = rows[-1]
        assert latest.username == "testuser"
        assert latest.resource_id == "h1"
        assert latest.details["status"] == "sent"

    def test_reload_emit_failure_audited_and_reraised(
        self, client, auth_headers, db_session, monkeypatch
    ):
        import pytest as _pytest
        from unittest.mock import AsyncMock, patch

        # TestClient 默认 raise_server_exceptions：服务端异常直接穿透到测试，
        # 断言异常传播 + 审计已在重抛前落库（生产侧即 500）。
        with patch(
            "backend.realtime.socketio_server.emit_agent_control",
            new=AsyncMock(side_effect=RuntimeError("agent offline")),
        ):
            with _pytest.raises(RuntimeError):
                client.post(
                    "/api/v1/plan-runs/hosts/h1/reload-config", headers=auth_headers
                )

        from backend.models.audit import AuditLog

        latest = (
            db_session.query(AuditLog)
            .filter(AuditLog.action == "agent_config_reload")
            .order_by(AuditLog.id.desc())
            .first()
        )
        assert latest is not None, "emit 失败路径审计行缺失"
        assert latest.details["reason"] == "emit_failed:RuntimeError"


class TestJiraProjectKeyWiring:
    """G17：source=plan_run 解析登记簿 jira_project_key 并注入 stage1 argv。"""

    @staticmethod
    def _seed_plan_run(db_session, *, jira_project_key=None):
        """TestProject → Plan → PlanRun → PlanRunArtifact 四层种子，返回 artifact id。

        PlanRun 带 project_id 冻结值（模拟 dispatcher 快照语义）——解析必须
        读 run.project_id，而不是当前 Plan 的归属。
        """
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
            project_id=project.id,
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

    def test_snapshot_key_wins_over_current_plan_attachment(
        self, client, auth_headers, monkeypatch, mock_run_console, db_session
    ):
        """快照语义：run.project_id 冻结值优先——Plan 事后改归属不影响历史 Run 的 JIRA 目标。"""
        from backend.models.plan import Plan
        from backend.models.plan_run import PlanRun
        from backend.models.project import TestProject

        artifact_id, run_id = self._seed_plan_run(db_session, jira_project_key="SNAPKEY")
        _set_vendor_env(monkeypatch)
        run = db_session.get(PlanRun, run_id)
        plan = db_session.get(Plan, run.plan_id)
        moved = TestProject(
            project_key="moved-to", display_name="moved", jira_project_key="NEWKEY"
        )
        db_session.add(moved)
        db_session.flush()
        plan.project_id = moved.id
        db_session.commit()

        resp = self._post_plan_run_source(client, auth_headers, artifact_id)
        assert resp.status_code == 200, resp.text
        argv = mock_run_console.start.call_args.kwargs.get("cmd", [])
        assert argv[argv.index("--set-project-key") + 1] == "SNAPKEY"
        assert resp.json()["data"]["jira_project_key"] == "SNAPKEY"


# ── #1084：先写后启 —— 快速回调不再产生永久 RUNNING ──────────────────────


class TestJiraRunPersistBeforeStart:
    def test_fast_completion_finds_row(self, client, auth_headers, monkeypatch, mock_run_console, db_session):
        """子进程秒级结束：on_complete 在 INSERT 之后仍能找到行并写终态。

        旧顺序（先 start 后 INSERT）下回调早于 INSERT 会找不到行直接跳过，
        jira_run 永久停在 RUNNING 且缺 issue_keys。
        """
        _set_vendor_env(monkeypatch)
        started = {}

        def fake_start(*args, **kwargs):
            run_id = kwargs["run_id"]
            started["run_id"] = run_id
            # 模拟「子进程秒级结束」：start 内同步触发终态回调
            row = db_session.query(JiraRun).filter_by(console_run_id=run_id).one()
            assert row.status == "RUNNING", "start 前行必须已落库"
            from types import SimpleNamespace
            from backend.api.routes.dedup import _on_jira_run_complete

            fake_run = SimpleNamespace(run_id=run_id, to_status=lambda: {
                "status": "SUCCESS", "exit_code": 0,
                "ended_at": "2026-09-09T00:00:00+00:00", "error": None,
                "seq": 0,
            })
            _on_jira_run_complete(fake_run)
            return run_id

        mock_run_console.start.side_effect = fake_start
        resp = client.post(
            "/api/v1/jira/runs",
            data={"vendor": "transsion", "stage": "upload_list", "dry_run": "true"},
            files={"file": ("Result.xls", b"fake-xls", "application/vnd.ms-excel")},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]

        row = db_session.query(JiraRun).filter_by(
            console_run_id=data["console_run_id"],
        ).one()
        assert row.status == "SUCCESS", "快速回调必须落终态，不能停留 RUNNING"

    def test_start_failure_marks_row_failed(self, client, auth_headers, monkeypatch, mock_run_console, db_session):
        """start 失败（409/500）时行回写 FAILED，不留悬挂 RUNNING。"""
        _set_vendor_env(monkeypatch)
        mock_run_console.start.side_effect = RunKeyBusyError("busy")
        resp = client.post(
            "/api/v1/jira/runs",
            data={"vendor": "transsion", "stage": "upload_list", "dry_run": "true"},
            files={"file": ("Result.xls", b"fake-xls", "application/vnd.ms-excel")},
            headers=auth_headers,
        )
        assert resp.status_code == 409
        # start 被拒绝，但预生成的行存在且已回写 FAILED
        rows = db_session.query(JiraRun).all()
        assert len(rows) == 1
        assert rows[0].status == "FAILED"
        assert "already in progress" in (rows[0].error or "")
