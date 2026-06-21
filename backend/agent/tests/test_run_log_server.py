"""Agent 运行日志 HTTP 下载端点测试。"""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from backend.agent.run_log_server import create_app


@pytest.fixture
def log_dir(tmp_path):
    d = tmp_path / "runs"
    d.mkdir()
    return d


@pytest.fixture
def client(log_dir):
    app = create_app(str(log_dir))
    return TestClient(app)


def test_list_run_logs(client, log_dir):
    job_dir = log_dir / "123"
    job_dir.mkdir()
    (job_dir / "init_check.log").write_text("hello", encoding="utf-8")
    (job_dir / "patrol_1.log").write_text("patrol", encoding="utf-8")

    resp = client.get("/run-logs/123")
    assert resp.status_code == 200
    data = resp.json()
    assert data["job_id"] == 123
    assert data["files"] == ["init_check.log", "patrol_1.log"]


def test_download_run_log(client, log_dir):
    job_dir = log_dir / "456"
    job_dir.mkdir()
    (job_dir / "test.log").write_text("log content", encoding="utf-8")

    resp = client.get("/run-logs/456/test.log")
    assert resp.status_code == 200
    assert resp.text == "log content"


def test_path_traversal_blocked(client):
    resp = client.get("/run-logs/../../etc/passwd")
    assert resp.status_code in (403, 404)


def test_missing_file_404(client, log_dir):
    job_dir = log_dir / "789"
    job_dir.mkdir()
    resp = client.get("/run-logs/789/nonexistent.log")
    assert resp.status_code == 404


def test_missing_dir_404(client):
    resp = client.get("/run-logs/999")
    assert resp.status_code == 404
