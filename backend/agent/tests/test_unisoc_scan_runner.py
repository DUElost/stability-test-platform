"""Tests for UnisocScanRunner argv / configure (ADR-0032 D4c)."""

from __future__ import annotations

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
