"""Regression tests for backend/agent/scripts/flash_firmware/v1.0.0/flash_firmware.py.

Loaded via importlib because the script lives outside the backend.agent package
(scripts/<name>/v<version>/ layout is not a valid Python module path).
"""

from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest


_FLASH_FIRMWARE_PY = (
    Path(__file__).resolve().parent.parent
    / "scripts" / "flash_firmware" / "v1.0.0" / "flash_firmware.py"
)


@pytest.fixture(scope="module")
def flash_firmware():
    """Load the standalone flash_firmware.py as a module for direct invocation."""
    spec = importlib.util.spec_from_file_location(
        "flash_firmware_under_test", _FLASH_FIRMWARE_PY
    )
    assert spec and spec.loader, f"cannot locate {_FLASH_FIRMWARE_PY}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fake_completed(stdout: str = "", stderr: str = "", returncode: int = 0):
    return subprocess.CompletedProcess(
        args=["adb"], returncode=returncode, stdout=stdout, stderr=stderr
    )


# ---------------------------------------------------------------------------
# _build_subprocess_env: LD_LIBRARY_PATH injection mirrors flash_tool.sh
# ---------------------------------------------------------------------------


class TestBuildSubprocessEnv:
    def test_linux_no_existing_library_path(self, flash_firmware, monkeypatch):
        monkeypatch.setattr(flash_firmware.platform, "system", lambda: "Linux")
        monkeypatch.delenv("LD_LIBRARY_PATH", raising=False)
        env = flash_firmware._build_subprocess_env("/opt/foo/flashtool")
        assert env["LD_LIBRARY_PATH"] == "/opt/foo/flashtool:/opt/foo/flashtool/lib"

    def test_linux_preserves_existing_library_path(self, flash_firmware, monkeypatch):
        monkeypatch.setattr(flash_firmware.platform, "system", lambda: "Linux")
        monkeypatch.setenv("LD_LIBRARY_PATH", "/usr/local/lib")
        env = flash_firmware._build_subprocess_env("/opt/foo/flashtool")
        assert env["LD_LIBRARY_PATH"] == "/opt/foo/flashtool:/opt/foo/flashtool/lib:/usr/local/lib"

    def test_windows_does_not_inject(self, flash_firmware, monkeypatch):
        monkeypatch.setattr(flash_firmware.platform, "system", lambda: "Windows")
        monkeypatch.delenv("LD_LIBRARY_PATH", raising=False)
        env = flash_firmware._build_subprocess_env("C:\\opt\\flashtool")
        assert "LD_LIBRARY_PATH" not in env


# ---------------------------------------------------------------------------
# _adb_device_state: probe adb get-state safely
# ---------------------------------------------------------------------------


class TestAdbDeviceState:
    def test_no_serial_returns_no_device(self, flash_firmware):
        assert flash_firmware._adb_device_state("", "adb") == "no-device"

    def test_no_adb_path_returns_no_device(self, flash_firmware):
        assert flash_firmware._adb_device_state("abc", "") == "no-device"

    def test_returncode_nonzero_means_no_device(self, flash_firmware, monkeypatch):
        monkeypatch.setattr(
            flash_firmware.subprocess, "run",
            lambda *a, **kw: _fake_completed(stderr="error: no devices", returncode=1),
        )
        assert flash_firmware._adb_device_state("xyz", "adb") == "no-device"

    def test_device_state_stripped(self, flash_firmware, monkeypatch):
        monkeypatch.setattr(
            flash_firmware.subprocess, "run",
            lambda *a, **kw: _fake_completed(stdout="device\n", returncode=0),
        )
        assert flash_firmware._adb_device_state("xyz", "adb") == "device"

    def test_filenotfound_returns_unknown(self, flash_firmware, monkeypatch):
        def boom(*a, **kw):
            raise FileNotFoundError("adb missing")
        monkeypatch.setattr(flash_firmware.subprocess, "run", boom)
        assert flash_firmware._adb_device_state("xyz", "adb") == "unknown"

    def test_timeout_returns_unknown(self, flash_firmware, monkeypatch):
        def boom(*a, **kw):
            raise subprocess.TimeoutExpired(cmd="adb", timeout=5)
        monkeypatch.setattr(flash_firmware.subprocess, "run", boom)
        assert flash_firmware._adb_device_state("xyz", "adb") == "unknown"


# ---------------------------------------------------------------------------
# _reboot_into_flash_mode: best-effort; only "device" triggers adb reboot
# ---------------------------------------------------------------------------


class TestRebootIntoFlashMode:
    def test_skip_when_serial_missing(self, flash_firmware):
        r = flash_firmware._reboot_into_flash_mode("", "bootloader", "adb", 0)
        assert r == {
            "attempted": False,
            "target": "bootloader",
            "skip_reason": "STP_DEVICE_SERIAL not set",
        }

    def test_skip_when_adb_path_missing(self, flash_firmware):
        r = flash_firmware._reboot_into_flash_mode("abc", "bootloader", "", 0)
        assert r["attempted"] is False
        assert "STP_ADB_PATH" in r["skip_reason"]

    @pytest.mark.parametrize(
        "non_ready_state",
        ["offline", "unauthorized", "no-device", "unknown", "bootloader"],
    )
    def test_skip_when_state_not_device(self, flash_firmware, monkeypatch, non_ready_state):
        """offline / unauthorized / no-device / unknown all bypass adb reboot."""
        monkeypatch.setattr(
            flash_firmware, "_adb_device_state",
            lambda serial, adb_path: non_ready_state,
        )
        sentinel = {"reboot_called": False}

        def fake_run(*a, **kw):
            sentinel["reboot_called"] = True
            return _fake_completed(returncode=0)

        monkeypatch.setattr(flash_firmware.subprocess, "run", fake_run)
        r = flash_firmware._reboot_into_flash_mode("abc", "bootloader", "adb", 0)
        assert sentinel["reboot_called"] is False, "adb reboot must not run for non-device state"
        assert r["attempted"] is False
        assert r["pre_state"] == non_ready_state
        assert "flash_tool will wait on USB" in r["skip_reason"]

    def test_reboot_invoked_when_state_device(self, flash_firmware, monkeypatch):
        monkeypatch.setattr(flash_firmware, "_adb_device_state", lambda *a, **k: "device")
        recorded = {}

        def fake_run(cmd, **kwargs):
            recorded["cmd"] = cmd
            recorded["timeout"] = kwargs.get("timeout")
            return _fake_completed(returncode=0)

        monkeypatch.setattr(flash_firmware.subprocess, "run", fake_run)
        slept = {}
        monkeypatch.setattr(flash_firmware.time, "sleep", lambda s: slept.setdefault("seconds", s))

        r = flash_firmware._reboot_into_flash_mode("abc", "preloader", "/usr/bin/adb", 3)
        assert recorded["cmd"] == ["/usr/bin/adb", "-s", "abc", "reboot", "preloader"]
        assert recorded["timeout"] == 15
        assert slept["seconds"] == 3
        assert r["attempted"] is True
        assert r["pre_state"] == "device"
        assert r["exit_code"] == 0
        assert r["waited_seconds"] == 3

    def test_reboot_nonzero_captured(self, flash_firmware, monkeypatch):
        monkeypatch.setattr(flash_firmware, "_adb_device_state", lambda *a, **k: "device")
        monkeypatch.setattr(
            flash_firmware.subprocess, "run",
            lambda *a, **kw: _fake_completed(stderr="adbd refused", returncode=1),
        )
        monkeypatch.setattr(flash_firmware.time, "sleep", lambda s: None)
        r = flash_firmware._reboot_into_flash_mode("abc", "bootloader", "adb", 0)
        assert r["attempted"] is True
        assert r["exit_code"] == 1
        assert "adbd refused" in r["stderr_tail"]


# ---------------------------------------------------------------------------
# _scan_output_for_verdict: fail-token wins over pass-token
# ---------------------------------------------------------------------------


class TestScanOutputForVerdict:
    def test_pass_token_present(self, flash_firmware):
        ok, evidence = flash_firmware._scan_output_for_verdict("All command exec done", "")
        assert ok is True and "pass token hit" in evidence

    def test_fail_token_overrides_pass(self, flash_firmware):
        ok, evidence = flash_firmware._scan_output_for_verdict(
            "All command exec done", "S_DA_HANDSHAKE_FAILED"
        )
        assert ok is False and "fail token" in evidence

    def test_no_token_means_fail(self, flash_firmware):
        ok, evidence = flash_firmware._scan_output_for_verdict("...random output...", "")
        assert ok is False and evidence == "no pass token found"


# ---------------------------------------------------------------------------
# _pick_flash_tool_exe: supports both Linux flat and Windows-nested layout
# ---------------------------------------------------------------------------


class TestPickFlashToolExe:
    def test_flat_linux_layout(self, flash_firmware, tmp_path):
        (tmp_path / "flash_tool").write_bytes(b"\x7fELF")
        assert flash_firmware._pick_flash_tool_exe(str(tmp_path)) == str(tmp_path / "flash_tool")

    def test_nested_windows_layout(self, flash_firmware, tmp_path):
        nested = tmp_path / "SP_Flash_Tool_V5"
        nested.mkdir()
        exe = nested / "flash_tool.exe"
        exe.write_bytes(b"MZ")
        assert flash_firmware._pick_flash_tool_exe(str(tmp_path)) == str(exe)

    def test_returns_none_when_missing(self, flash_firmware, tmp_path):
        assert flash_firmware._pick_flash_tool_exe(str(tmp_path)) is None


# ---------------------------------------------------------------------------
# main(): top-level JSON contract — validation path (no flash_tool invocation)
# ---------------------------------------------------------------------------


class TestMainValidationPath:
    def _run_main_capture(self, flash_firmware, monkeypatch, step_params: dict) -> dict:
        monkeypatch.setenv("STP_STEP_PARAMS", json.dumps(step_params))
        buf = io.StringIO()
        monkeypatch.setattr(sys, "stdout", buf)
        try:
            flash_firmware.main()
        finally:
            monkeypatch.setattr(sys, "stdout", sys.__stdout__)
        return json.loads(buf.getvalue().strip())

    def test_missing_firmware_dir(self, flash_firmware, monkeypatch):
        out = self._run_main_capture(flash_firmware, monkeypatch, {})
        assert out == {
            "success": False,
            "skipped": False,
            "error_message": "firmware_dir is required",
        }

    def test_missing_da_file(self, flash_firmware, monkeypatch):
        out = self._run_main_capture(flash_firmware, monkeypatch, {"firmware_dir": "x"})
        assert out["success"] is False
        assert out["error_message"] == "da_file is required"

    def test_missing_scatter_file(self, flash_firmware, monkeypatch):
        out = self._run_main_capture(
            flash_firmware, monkeypatch,
            {"firmware_dir": "x", "da_file": "a"},
        )
        assert out["success"] is False
        assert out["error_message"] == "scatter_file is required"

    def test_firmware_dir_not_found(self, flash_firmware, monkeypatch, tmp_path):
        out = self._run_main_capture(
            flash_firmware, monkeypatch,
            {
                "firmware_dir": str(tmp_path / "nope"),
                "da_file": "a",
                "scatter_file": "b",
            },
        )
        assert out["success"] is False
        assert "firmware_dir not found" in out["error_message"]
