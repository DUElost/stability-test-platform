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


class TestPollSecondsBadConfig:
    """#754：STP_UNISOC_LOG_SCAN_POLL_SECONDS 误配不得杀死 scan 队列 worker。"""

    @pytest.mark.parametrize(
        "raw",
        ["not-a-number", "60s", " ", "", "-5", "0", "1.5"],
    )
    def test_bad_value_falls_back_to_default(self, monkeypatch, raw):
        monkeypatch.setenv("STP_UNISOC_LOG_SCAN_POLL_SECONDS", raw)
        assert UnisocScanRunner._poll_seconds() == 60

    def test_missing_value_falls_back_to_default(self, monkeypatch):
        monkeypatch.delenv("STP_UNISOC_LOG_SCAN_POLL_SECONDS", raising=False)
        assert UnisocScanRunner._poll_seconds() == 60

    def test_valid_value_is_honoured(self, monkeypatch):
        monkeypatch.setenv("STP_UNISOC_LOG_SCAN_POLL_SECONDS", "45")
        assert UnisocScanRunner._poll_seconds() == 45

    def test_bad_value_does_not_raise_from_build_argv(self, monkeypatch):
        monkeypatch.setenv("STP_UNISOC_LOG_SCAN_POLL_SECONDS", "not-a-number")
        runner = UnisocScanRunner.instance()
        runner.configure(
            scan_tool_python="/usr/bin/python3",
            scan_tool_script="/tools/scan_log_gt.py",
            result_python="/usr/bin/python3",
            result_script="/tools/scan_result.py",
            force=True,
        )
        argv = runner._build_argv(scan_root="/tmp/x")
        assert argv[-1] == "60"

    def test_bad_value_does_not_raise_from_run_log_scan_gt(self, monkeypatch, tmp_path):
        """回归：#754 原始故障——int() 打穿 _run_log_scan_gt。"""
        import subprocess as sp

        monkeypatch.setenv("STP_UNISOC_LOG_SCAN_POLL_SECONDS", "not-a-number")
        runner = UnisocScanRunner.instance()
        runner.configure(
            scan_tool_python="/usr/bin/python3",
            scan_tool_script="/tools/scan_log_gt.py",
            result_python="/usr/bin/python3",
            result_script="/tools/scan_result.py",
            force=True,
        )
        monkeypatch.setattr(
            "backend.agent.unisoc_scan_runner.subprocess.run",
            lambda *a, **k: sp.CompletedProcess(args=a[0] if a else [], returncode=0),
        )
        # 修复前：ValueError 逃逸（进而杀死 worker 线程）；修复后：正常返回
        assert runner._run_log_scan_gt(str(tmp_path), 1, "host") is True


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

        assert runner.run_scan_result(str(scan_root), 1, "host", scan_start=0.0) is None

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

        assert runner.run_scan_result(str(scan_root), 1, "host", scan_start=0.0) == str(org.resolve())

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


class TestScanStartWatermark:
    """#760: 增量扫描不得把上轮 *_org.xls 当本轮产物。"""

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

    def test_run_scan_result_rejects_stale_org_only(self, tmp_path, monkeypatch):
        import os
        import time

        runner = self._configured(monkeypatch)
        scan_root = tmp_path / "scan"
        scan_root.mkdir()
        stale = scan_root / "Result_old_org.xls"
        stale.write_bytes(b"old-report")
        # 把 mtime 拨到「扫描启动」之前
        past = time.time() - 120
        os.utime(stale, (past, past))

        fake = MagicMock(returncode=0, stderr="")
        monkeypatch.setattr(
            "backend.agent.unisoc_scan_runner.subprocess.run",
            lambda *a, **k: fake,
        )
        scan_start = time.time()
        assert runner.run_scan_result(
            str(scan_root), 1, "host", scan_start=scan_start,
        ) is None

    def test_run_scan_result_prefers_fresh_over_stale(self, tmp_path, monkeypatch):
        import os
        import time

        runner = self._configured(monkeypatch)
        scan_root = tmp_path / "scan"
        scan_root.mkdir()
        stale = scan_root / "Result_old_org.xls"
        stale.write_bytes(b"old-report-larger-mtime-trick")
        past = time.time() - 120
        os.utime(stale, (past, past))

        scan_start = time.time()
        fresh = scan_root / "Result_new_org.xls"
        fresh.write_bytes(b"new")

        fake = MagicMock(returncode=0, stderr="")
        monkeypatch.setattr(
            "backend.agent.unisoc_scan_runner.subprocess.run",
            lambda *a, **k: fake,
        )
        assert runner.run_scan_result(
            str(scan_root), 1, "host", scan_start=scan_start,
        ) == str(fresh.resolve())


class TestPackagePlane:
    """ADR-0051 Phase 4a：显式传参 > 包面（*PACKAGE_REF）> env 路径键；空键 = 整体 no-op。"""

    def _pkg(self, python, script):
        from backend.agent.tool_cache import PackageTool

        return PackageTool(name="X", version="1", python=python, script=script)

    def test_package_plane_replaces_env_keys(self, monkeypatch):
        import backend.agent.unisoc_scan_runner as mod

        mod.UnisocScanRunner._instance = None
        monkeypatch.setenv("STP_UNISOC_LOG_SCAN_PYTHON", "/usr/bin/python3")
        monkeypatch.setenv("STP_UNISOC_LOG_SCAN_SCRIPT", "/tools/log/scan_log_gt.py")
        monkeypatch.setenv("STP_UNISOC_SCAN_RESULT_PYTHON", "/usr/bin/python3")
        monkeypatch.setenv("STP_UNISOC_SCAN_RESULT_SCRIPT", "/tools/res/scan_result.py")
        calls = []

        def fake_resolve(key, env=None):
            calls.append(key)
            if key == "STP_UNISOC_LOG_SCAN_PACKAGE_REF":
                return self._pkg("/venv/py", "/cache/log/scan_log_gt.py")
            return self._pkg("/venv/py", "/cache/res/scan_result.py")

        monkeypatch.setattr(mod, "resolve_packaged_tool", fake_resolve)
        runner = mod.UnisocScanRunner.instance()
        runner.configure(force=True)
        assert calls == ["STP_UNISOC_LOG_SCAN_PACKAGE_REF", "STP_UNISOC_SCAN_RESULT_PACKAGE_REF"]
        assert runner._scan_script == "/cache/log/scan_log_gt.py"
        assert runner._result_script == "/cache/res/scan_result.py"

    def test_explicit_params_win_over_package(self, monkeypatch):
        import backend.agent.unisoc_scan_runner as mod

        mod.UnisocScanRunner._instance = None
        monkeypatch.setattr(mod, "resolve_packaged_tool",
                            lambda key, env=None: pytest.fail(f"显式传参时不得触包面: {key}"))
        runner = mod.UnisocScanRunner.instance()
        runner.configure(scan_tool_python="p1", scan_tool_script="s1",
                         result_python="p2", result_script="s2", force=True)
        assert runner._scan_python == "p1" and runner._result_script == "s2"

    def test_no_package_falls_back_to_env(self, monkeypatch):
        import backend.agent.unisoc_scan_runner as mod

        mod.UnisocScanRunner._instance = None
        monkeypatch.setattr(mod, "resolve_packaged_tool", lambda key, env=None: None)
        monkeypatch.setenv("STP_UNISOC_LOG_SCAN_PYTHON", "lp")
        monkeypatch.setenv("STP_UNISOC_LOG_SCAN_SCRIPT", "ls")
        monkeypatch.setenv("STP_UNISOC_SCAN_RESULT_PYTHON", "rp")
        monkeypatch.setenv("STP_UNISOC_SCAN_RESULT_SCRIPT", "rs")
        runner = mod.UnisocScanRunner.instance()
        runner.configure(force=True)
        assert runner._scan_python == "lp" and runner._result_python == "rp" and runner.is_configured()

    def test_half_package_none_keeps_env_pair(self, monkeypatch):
        """result 有包、log 无包：log 保持 env 值——两槽独立。"""
        import backend.agent.unisoc_scan_runner as mod

        mod.UnisocScanRunner._instance = None
        monkeypatch.setattr(mod, "resolve_packaged_tool",
                            lambda key, env=None: self._pkg("x", "pkg-r") if "RESULT" in key else None)
        monkeypatch.setenv("STP_UNISOC_LOG_SCAN_PYTHON", "lp")
        monkeypatch.setenv("STP_UNISOC_LOG_SCAN_SCRIPT", "ls")
        runner = mod.UnisocScanRunner.instance()
        runner.configure(force=True)
        assert runner._scan_python == "lp" and runner._result_python == "x"
