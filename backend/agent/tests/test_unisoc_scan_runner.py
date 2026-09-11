"""Tests for UnisocScanRunner argv / configure (ADR-0032 D4c)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from backend.agent.unisoc_scan_runner import UnisocScanRunner


@pytest.fixture(autouse=True)
def _reset_runner():
    UnisocScanRunner._reset_for_tests()
    yield
    UnisocScanRunner._reset_for_tests()


def test_configure_requires_all_four_tool_paths():
    runner = UnisocScanRunner.instance()
    assert not runner.is_configured()
    runner.configure(
        scan_tool_python="/usr/bin/python3",
        scan_tool_script="/tools/scan_log_gt.py",
        force=True,
    )
    assert not runner.is_configured()
    runner.configure(
        scan_tool_python="/usr/bin/python3",
        scan_tool_script="/tools/scan_log_gt.py",
        result_python="/usr/bin/python3",
        result_script="/tools/scan_result.py",
        force=True,
    )
    assert runner.is_configured()


def test_build_argv_uses_scan_root_sprd_and_poll_interval(monkeypatch):
    monkeypatch.setenv("STP_UNISOC_LOG_SCAN_POLL_SECONDS", "45")
    runner = UnisocScanRunner.instance()
    runner.configure(
        scan_tool_python="/usr/bin/python3",
        scan_tool_script="/mnt/stp-aee/tools/Monkey-Log-Scan-GT-SPRD/scan_log_gt.py",
        result_python="/usr/bin/python3",
        result_script="/mnt/stp-aee/tools/Scan-Result-GT/scan_result.py",
        force=True,
    )
    assert runner._build_argv(scan_root="/tmp/stp-scan/pr1-abc") == [
        "/usr/bin/python3",
        "/mnt/stp-aee/tools/Monkey-Log-Scan-GT-SPRD/scan_log_gt.py",
        "-p",
        "/tmp/stp-scan/pr1-abc",
        "-m",
        "sprd",
        "-i",
        "45",
    ]


class TestTimeoutArtifactIntegrity:
    """#805-5：超时终止后的半成品不得冒充完整产物上送。"""

    def _configured(self, monkeypatch):
        runner = UnisocScanRunner.instance()
        runner.configure(
            scan_tool_python="/usr/bin/python3",
            scan_tool_script="/tools/scan_log_gt.py",
            result_python="/usr/bin/python3",
            result_script="/tools/scan_result.py",
            force=True,
        )
        return runner

    def test_artifact_complete_requires_nonempty(self, tmp_path):
        f = tmp_path / "a.xls"
        f.write_bytes(b"")
        assert UnisocScanRunner._artifact_looks_complete(f) is False

    def test_artifact_complete_for_stable_nonempty(self, tmp_path):
        f = tmp_path / "a.xls"
        f.write_bytes(b"0123456789")
        assert UnisocScanRunner._artifact_looks_complete(f) is True

    def test_run_scan_result_rejects_incomplete_after_timeout(self, tmp_path, monkeypatch):
        runner = self._configured(monkeypatch)
        scan_root = tmp_path / "scan"
        scan_root.mkdir()
        org = scan_root / "Result_x_org.xls"
        org.write_bytes(b"")  # 半成品：空文件

        fake = MagicMock(returncode=0, stderr="")
        monkeypatch.setattr(
            "backend.agent.unisoc_scan_runner.subprocess.run",
            lambda *a, **k: fake,
        )
        runner._last_scan_timed_out = True

        assert runner.run_scan_result(str(scan_root), 1, "host") is None

    def test_run_scan_result_accepts_complete_after_timeout(self, tmp_path, monkeypatch):
        runner = self._configured(monkeypatch)
        scan_root = tmp_path / "scan"
        scan_root.mkdir()
        org = scan_root / "Result_x_org.xls"
        org.write_bytes(b"full-report-bytes")

        fake = MagicMock(returncode=0, stderr="")
        monkeypatch.setattr(
            "backend.agent.unisoc_scan_runner.subprocess.run",
            lambda *a, **k: fake,
        )
        runner._last_scan_timed_out = True

        assert runner.run_scan_result(str(scan_root), 1, "host") == str(org.resolve())

    def test_timeout_sets_flag(self, tmp_path, monkeypatch):
        import subprocess as sp

        runner = self._configured(monkeypatch)

        def boom(*a, **k):
            raise sp.TimeoutExpired(cmd=["x"], timeout=1)

        monkeypatch.setattr(
            "backend.agent.unisoc_scan_runner.subprocess.run", boom,
        )
        assert runner._run_log_scan_gt(str(tmp_path), 1, "host") is True
        assert runner._last_scan_timed_out is True
