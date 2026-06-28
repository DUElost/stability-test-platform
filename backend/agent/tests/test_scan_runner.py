"""ScanRunner 单测（ADR-0025 Sprint 4 Task 1）。

覆盖面：
  1. run_local_scan 以 -dedup_org 调用 start_log_scan.py
  2. 未 configure 时返回 None
  3. subprocess 返回非零时返回 None
  4. org.xls 未找到时返回 None
  5. subprocess 超时时返回 None
  6. configure 环境变量降级
  7. _build_argv 含/不含 -end
  8. 重复 configure 被忽略
"""

from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backend.agent.scan_runner import ScanRunner


@pytest.fixture(autouse=True)
def _reset_scan_runner():
    ScanRunner._reset_for_tests()
    yield
    ScanRunner._reset_for_tests()


def _make_runner() -> ScanRunner:
    r = ScanRunner.instance()
    r.configure(
        scan_tool_python="/usr/bin/python3",
        scan_tool_script="/opt/scan/start_log_scan.py",
        hdd_root="/mnt/hdd/aee_events",
        side="shanghai",
    )
    assert r.is_configured()
    return r


def _completed(returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=[],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


def test_run_local_scan_calls_start_log_scan_with_aee_tne_mode(tmp_path):
    r = _make_runner()
    hdd = tmp_path / "hdd"
    hdd.mkdir()
    org_xls = hdd / "Result_shanghai_org.xls"
    org_xls.write_text("fake")
    r._hdd_root = str(hdd)

    with patch("backend.agent.scan_runner.subprocess.run") as mock_run:
        mock_run.return_value = _completed(stdout="done")
        result = r.run_local_scan(42, "host-1")

    assert result is not None
    assert "Result_shanghai_org.xls" in result
    called_argv = mock_run.call_args[0][0]
    assert "-m" in called_argv
    assert "0" in called_argv
    assert "-d" in called_argv
    assert str(hdd) in called_argv
    assert "-side" in called_argv


def test_run_local_scan_not_configured():
    r = ScanRunner.instance()
    assert not r.is_configured()
    result = r.run_local_scan(1, "host-1")
    assert result is None


def test_run_local_scan_tool_failure():
    r = _make_runner()
    with patch("backend.agent.scan_runner.subprocess.run") as mock_run:
        mock_run.return_value = _completed(returncode=1, stderr="error")
        result = r.run_local_scan(1, "host-1", is_final=True)
    assert result is None


def test_run_local_scan_no_org_xls_found(tmp_path):
    r = _make_runner()
    r._hdd_root = str(tmp_path)
    with patch("backend.agent.scan_runner.subprocess.run") as mock_run:
        mock_run.return_value = _completed(returncode=0, stdout="done")
        result = r.run_local_scan(1, "host-1")
    assert result is None


def test_run_local_scan_timeout():
    r = _make_runner()
    with patch("backend.agent.scan_runner.subprocess.run") as mock_run:
        mock_run.side_effect = subprocess.TimeoutExpired(cmd=[], timeout=600)
        result = r.run_local_scan(1, "host-1")
    assert result is None


def test_configure_env_fallback(monkeypatch):
    monkeypatch.setenv("STP_DEDUP_SCAN_PYTHON", "/env/python3")
    monkeypatch.setenv("STP_DEDUP_SCAN_SCRIPT", "/env/scan.py")
    r = ScanRunner.instance()
    with patch("backend.agent.scan_runner.get_aee_local_root", return_value=Path("/env/hdd")):
        r.configure()
    assert r.is_configured()
    assert r._scan_tool_python == "/env/python3"
    assert r._scan_tool_script == "/env/scan.py"


def test_configure_rejected_if_already_configured():
    r = _make_runner()
    first_python = r._scan_tool_python
    r.configure(scan_tool_python="/different/python")
    assert r._scan_tool_python == first_python


def test_build_argv_includes_end_flag():
    r = _make_runner()
    argv = r._build_argv(is_final=True)
    assert argv[-1] == "-end"
    assert "-m" in argv
    assert "-d" in argv
    assert "-side" in argv


def test_build_argv_without_end_flag():
    r = _make_runner()
    argv = r._build_argv(is_final=False)
    assert "-end" not in argv
    assert "-m" in argv


def test_host_scan_semaphore_limits_concurrency():
    _make_runner()
    assert ScanRunner.try_begin_host_scan() is True
    assert ScanRunner.try_begin_host_scan() is False
    ScanRunner.end_host_scan()
    assert ScanRunner.try_begin_host_scan() is True
    ScanRunner.end_host_scan()


def test_enqueue_coalesces_same_plan_run():
    _make_runner()
    with patch.object(ScanRunner, "_ensure_worker"):
        ScanRunner.enqueue_scan_now(55, "host-1", is_final=False)
        ScanRunner.enqueue_scan_now(55, "host-1", is_final=False)
        ScanRunner.enqueue_scan_now(55, "host-1", is_final=True)
        assert ScanRunner.pending_count() == 1
        job = ScanRunner._dequeue_next()
        assert job is not None
        assert job.plan_run_id == 55
        assert job.is_final is True


def test_enqueue_fifo_preserves_distinct_plan_runs():
    _make_runner()
    with patch.object(ScanRunner, "_ensure_worker"):
        ScanRunner.enqueue_scan_now(55, "host-1", is_final=False)
        ScanRunner.enqueue_scan_now(56, "host-1", is_final=True)
        assert ScanRunner.pending_count() == 2
        first = ScanRunner._dequeue_next()
        second = ScanRunner._dequeue_next()
        assert first is not None and first.plan_run_id == 55
        assert second is not None and second.plan_run_id == 56


def test_worker_runs_queued_job_after_active_scan():
    r = _make_runner()
    executed: list[tuple[int, bool]] = []
    gate = threading.Event()

    def slow_scan(self, plan_run_id: int, host_id: str, *, is_final: bool = False):
        executed.append((plan_run_id, is_final))
        if len(executed) == 1:
            gate.wait(timeout=2)

    with patch.object(ScanRunner, "run_scan_and_upload", slow_scan):
        ScanRunner.enqueue_scan_now(55, "host-1", is_final=False)
        deadline = time.time() + 1
        while time.time() < deadline and not executed:
            time.sleep(0.02)
        ScanRunner.enqueue_scan_now(55, "host-1", is_final=True)
        assert ScanRunner.pending_count() == 1
        gate.set()
        deadline = time.time() + 3
        while time.time() < deadline and len(executed) < 2:
            time.sleep(0.05)
    assert executed == [(55, False), (55, True)]


def test_run_local_scan_returns_none_when_no_fresh_xls(tmp_path):
    r = _make_runner()
    hdd = tmp_path / "hdd"
    hdd.mkdir()
    old_xls = hdd / "Result_shanghai_org.xls"
    old_xls.write_text("old")
    import os
    old_time = os.stat(old_xls).st_mtime - 100
    os.utime(str(old_xls), (old_time, old_time))
    r._hdd_root = str(hdd)

    with patch("backend.agent.scan_runner.subprocess.run") as mock_run:
        mock_run.return_value = _completed(returncode=0, stdout="done")
        result = r.run_local_scan(1, "host-1")

    assert result is None


def test_configure_force_overrides_existing():
    r = _make_runner()
    assert r._scan_tool_python == "/usr/bin/python3"
    r.configure(scan_tool_python="/new/python", scan_tool_script="/new/scan.py", force=True)
    assert r._scan_tool_python == "/new/python"
    assert r._scan_tool_script == "/new/scan.py"


def test_run_dedup_org_calls_dedup_org(tmp_path):
    r = _make_runner()
    org_xls = tmp_path / "Result_test_org.xls"
    org_xls.write_text("fake")
    dedup_xls = tmp_path / "Result_test_org_dedup_org_20260624_000000.xls"
    dedup_xls.write_text("deduped")

    with patch("backend.agent.scan_runner.subprocess.run") as mock_run:
        mock_run.return_value = _completed(returncode=0, stdout=str(dedup_xls))
        result = r.run_dedup_org(str(org_xls), 42, "host-1")

    assert result is not None
    assert "dedup_org" in result
    called_argv = mock_run.call_args[0][0]
    assert "-dedup_org" in called_argv
    assert str(org_xls) in called_argv
    assert "-side" in called_argv


def test_run_dedup_org_tool_failure():
    r = _make_runner()
    with patch("backend.agent.scan_runner.subprocess.run") as mock_run:
        mock_run.return_value = _completed(returncode=1, stderr="error")
        result = r.run_dedup_org("/fake/path.xls", 1, "host-1")
    assert result is None
