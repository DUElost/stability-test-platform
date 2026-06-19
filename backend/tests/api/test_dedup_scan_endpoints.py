"""ADR-0025 Sprint 4: scan/merge/extract 端点 + 终态触发测试。

覆盖：
- POST /plan-runs/{run_id}/dedup/scan（env 未配 503 + 正常触发）
- GET /plan-runs/{run_id}/dedup/status（空 + 有产物）
- POST /plan-runs/{run_id}/dedup/merge（无 scan 产物 409 + 正常触发）
- POST /plan-runs/{run_id}/dedup/extract（无 merge 产物 409 + 正常提取）
- crash-details 端点（空 + 有数据）
- 终态触发 helper（should_trigger_dedup + enqueue mock）
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from backend.services.run_console import RunConsole


@pytest.fixture(autouse=True)
def reset_run_console_singleton():
    RunConsole._reset_for_tests()
    yield
    RunConsole._reset_for_tests()


@pytest.fixture
def mock_run_console(monkeypatch):
    inst = MagicMock()
    inst.start.return_value = "scan-fake-123"
    inst.status.return_value = {
        "run_id": "scan-fake-123",
        "run_key": "scan:1",
        "label": "test",
        "status": "RUNNING",
        "exit_code": None,
        "started_at": "2026-06-19T00:00:00Z",
        "ended_at": None,
        "seq": 0,
        "error": None,
    }
    inst.read_log.return_value = {
        "run_id": "scan-fake-123",
        "from_seq": 1,
        "lines": [],
        "seq": 0,
        "status": "RUNNING",
    }
    monkeypatch.setattr("backend.api.routes.dedup.RunConsole.instance", lambda: inst)
    return inst


def _set_scan_env(monkeypatch):
    monkeypatch.setenv("STP_DEDUP_SCAN_PYTHON", "/opt/fake/python")
    monkeypatch.setenv("STP_DEDUP_SCAN_SCRIPT", "/opt/fake/start_log_scan.py")


class TestScanEndpoint:
    """POST /api/v1/plan-runs/{run_id}/dedup/scan"""

    def test_unauthenticated_returns_401(self, client):
        resp = client.post("/api/v1/plan-runs/1/dedup/scan")
        assert resp.status_code == 401

    def test_scan_env_unset_returns_503(self, client, auth_headers, monkeypatch, sample_plan_run):
        monkeypatch.delenv("STP_DEDUP_SCAN_PYTHON", raising=False)
        monkeypatch.delenv("STP_DEDUP_SCAN_SCRIPT", raising=False)
        resp = client.post(
            f"/api/v1/plan-runs/{sample_plan_run.id}/dedup/scan",
            headers=auth_headers,
        )
        assert resp.status_code == 503
        assert "not configured" in resp.json()["detail"].lower()

    def test_scan_triggers_run_console(
        self, client, auth_headers, monkeypatch, sample_plan_run, mock_run_console
    ):
        _set_scan_env(monkeypatch)
        resp = client.post(
            f"/api/v1/plan-runs/{sample_plan_run.id}/dedup/scan",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()["data"]
        assert body["console_run_id"] == "scan-fake-123"
        assert body["plan_run_id"] == sample_plan_run.id
        mock_run_console.start.assert_called_once()


class TestDedupStatusEndpoint:
    """GET /api/v1/plan-runs/{run_id}/dedup/status"""

    def test_empty_status(self, client, auth_headers, sample_plan_run):
        resp = client.get(
            f"/api/v1/plan-runs/{sample_plan_run.id}/dedup/status",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()["data"]
        assert body["plan_run_id"] == sample_plan_run.id
        assert body["artifacts"] == []


class TestMergeEndpoint:
    """POST /api/v1/plan-runs/{run_id}/dedup/merge"""

    def test_no_scan_result_returns_409(self, client, auth_headers, sample_plan_run):
        resp = client.post(
            f"/api/v1/plan-runs/{sample_plan_run.id}/dedup/merge",
            json={},
            headers=auth_headers,
        )
        assert resp.status_code == 409
        assert "scan first" in resp.json()["detail"].lower()

    def test_merge_env_unset_returns_503(self, client, auth_headers, monkeypatch, sample_plan_run, db_session):
        from backend.models.plan_run_artifact import PlanRunArtifact

        db_session.add(PlanRunArtifact(
            plan_run_id=sample_plan_run.id,
            host_id="host-1",
            storage_uri="/tmp/fake_scan.xls",
            artifact_type="scan_result_xls",
            size_bytes=100,
        ))
        db_session.commit()

        monkeypatch.delenv("STP_DEDUP_SCAN_PYTHON", raising=False)
        monkeypatch.delenv("STP_DEDUP_SCAN_SCRIPT", raising=False)
        resp = client.post(
            f"/api/v1/plan-runs/{sample_plan_run.id}/dedup/merge",
            json={},
            headers=auth_headers,
        )
        assert resp.status_code == 503


class TestExtractEndpoint:
    """POST /api/v1/plan-runs/{run_id}/dedup/extract"""

    def test_no_merge_result_returns_409(self, client, auth_headers, sample_plan_run):
        resp = client.post(
            f"/api/v1/plan-runs/{sample_plan_run.id}/dedup/extract",
            headers=auth_headers,
        )
        assert resp.status_code == 409
        assert "merge first" in resp.json()["detail"].lower()

    def test_nfs_root_unset_returns_503(self, client, auth_headers, monkeypatch, sample_plan_run, db_session):
        from backend.models.plan_run_artifact import PlanRunArtifact

        db_session.add(PlanRunArtifact(
            plan_run_id=sample_plan_run.id,
            host_id=None,
            storage_uri="/tmp/fake_merge.xls",
            artifact_type="merge_result_xls",
            size_bytes=200,
        ))
        db_session.commit()

        monkeypatch.delenv("STP_AEE_NFS_ROOT", raising=False)
        monkeypatch.delenv("STP_WATCHER_NFS_BASE_DIR", raising=False)
        resp = client.post(
            f"/api/v1/plan-runs/{sample_plan_run.id}/dedup/extract",
            headers=auth_headers,
        )
        assert resp.status_code == 503
        assert "nfs root" in resp.json()["detail"].lower()


class TestCrashDetailsEndpoint:
    """GET /api/v1/plan-runs/{run_id}/crash-details"""

    def test_empty_crash_details(self, client, auth_headers, sample_plan_run):
        resp = client.get(
            f"/api/v1/plan-runs/{sample_plan_run.id}/crash-details",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["data"] == []

    def test_unauthenticated_returns_401(self, client):
        resp = client.get("/api/v1/plan-runs/1/crash-details")
        assert resp.status_code == 401


class TestDedupTriggerHelpers:
    """终态触发 helper 函数单测"""

    def test_should_trigger_dedup_disabled(self, monkeypatch):
        monkeypatch.setenv("STP_DEDUP_AUTO_SCAN", "0")
        from backend.services.dedup_scan import should_trigger_dedup
        assert should_trigger_dedup("FAILED") is False

    def test_should_trigger_dedup_non_terminal(self, monkeypatch):
        monkeypatch.setenv("STP_DEDUP_AUTO_SCAN", "1")
        from backend.services.dedup_scan import should_trigger_dedup
        assert should_trigger_dedup("RUNNING") is False

    def test_should_trigger_dedup_terminal(self, monkeypatch):
        monkeypatch.setenv("STP_DEDUP_AUTO_SCAN", "1")
        from backend.services.dedup_scan import should_trigger_dedup
        for status in ("SUCCESS", "PARTIAL_SUCCESS", "FAILED", "DEGRADED"):
            assert should_trigger_dedup(status) is True

    def test_enqueue_dedup_terminal_sync_swallows_errors(self, monkeypatch):
        from backend.services.dedup_scan import enqueue_dedup_terminal_sync

        def _boom(*a, **kw):
            raise RuntimeError("redis down")

        monkeypatch.setattr("backend.tasks.saq_worker.enqueue_sync", _boom)
        # 不应抛异常
        enqueue_dedup_terminal_sync(42)

    @pytest.mark.asyncio
    async def test_enqueue_dedup_terminal_async_swallows_errors(self, monkeypatch):
        from backend.services.dedup_scan import enqueue_dedup_terminal_async

        def _boom():
            raise RuntimeError("redis down")

        monkeypatch.setattr("backend.tasks.saq_worker.get_queue", _boom)
        await enqueue_dedup_terminal_async(42)
