"""flash_firmware v1.3.5：verify 预算默认 300s。

v1.3.4 行为由既有用例覆盖；这里只验证增量：
verify_wait_seconds 缺省预算从 180 上调到 300（`.87` hub 树实测刷后启动
回归普遍 >180s），参数显式覆盖仍然生效。
"""

from __future__ import annotations

import importlib.util
import json
import time as real_time
from pathlib import Path

import pytest


_SCRIPT_DIR = (
    Path(__file__).resolve().parents[2]
    / "agent" / "scripts" / "flash_firmware" / "v1.3.5"
)

spec = importlib.util.spec_from_file_location(
    "flash_firmware_v135", _SCRIPT_DIR / "flash_firmware.py"
)
ff = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(ff)


class _FakeTime:
    def __init__(self):
        self.sleeps: list = []

    def sleep(self, seconds):
        self.sleeps.append(seconds)

    def time(self):
        return real_time.time()

    def monotonic(self):
        return real_time.monotonic()


def _fw_dir(tmp_path: Path) -> Path:
    ver = tmp_path / "fw" / "MLD" / "9.0.0.1"
    ver.mkdir(parents=True)
    (ver / "scatter.txt").write_text("s", encoding="utf-8")
    (ver / "da.bin").write_text("d", encoding="utf-8")
    (ver / "manifest.json").write_text(json.dumps({
        "family": "MLD", "version": "9.0.0.1",
        "scatter_file": "scatter.txt", "da_file": "da.bin",
    }), encoding="utf-8")
    return ver


class TestVerifyBudgetDefault:
    @pytest.mark.parametrize("override,expected", [
        ({}, 300),                      # v1.3.5 核心变更：缺省 300
        ({"verify_wait_seconds": 60}, 60),   # 显式覆盖仍生效
        ({"verify_wait_seconds": "abc"}, 300),  # 坏值回落默认
    ])
    def test_verify_wait_budget(self, tmp_path, monkeypatch, capsys,
                                override, expected):
        fw_ver = _fw_dir(tmp_path)
        waits: list = []
        monkeypatch.setenv("STP_DEVICE_SERIAL", "SER1")
        monkeypatch.setenv("STP_STEP_PARAMS", json.dumps({
            "firmware_dir": str(fw_ver),
            "flash_tool_dir": str(fw_ver),
            **override,
        }))
        monkeypatch.setattr(ff, "_precheck_environment",
                            lambda exe, adb, need_adb, strict:
                            (True, {"items": [], "warnings": []}))
        monkeypatch.setattr(ff, "_pick_flash_tool_exe",
                            lambda tool_dir: str(fw_ver / "flash_tool"))
        monkeypatch.setattr(ff, "_is_linux", lambda: True)
        monkeypatch.setattr(ff, "_gate_other_mtk",
                            lambda tp, base=ff._SYSFS_USB_BASE:
                            {"hidden": [], "errors": {},
                             "skipped_reason": None, "target_port": tp})
        monkeypatch.setattr(ff, "_restore_gated",
                            lambda gated, base=ff._SYSFS_USB_BASE:
                            {"restored": [], "errors": {}})
        monkeypatch.setattr(ff, "_acquire_host_lock",
                            lambda on_wait_tick=None: object())
        monkeypatch.setattr(ff, "_release_host_lock", lambda fd: None)
        monkeypatch.setattr(ff, "_reboot_into_flash_mode",
                            lambda serial, target, adb_path, wait_seconds:
                            {"attempted": True})
        monkeypatch.setattr(ff, "_run_flash_tool_with_progress",
                            lambda cmd, cwd, env, timeout, on_stage,
                            on_percent, on_running=None:
                            ("All command exec done", 0))
        monkeypatch.setattr(ff, "_wait_device_back",
                            lambda serial, adb_path, timeout, on_tick: True)
        monkeypatch.setattr(ff, "_verify_after_flash",
                            lambda route, serial, adb, wait, on_tick:
                            waits.append(wait) or
                            (True, {"current": route.get("version")}))
        fake_time = _FakeTime()
        monkeypatch.setattr(ff, "time", fake_time)

        ff.main()
        payload = json.loads(capsys.readouterr().out.strip())
        assert payload["success"] is True, payload.get("error_message")
        assert waits == [expected]
