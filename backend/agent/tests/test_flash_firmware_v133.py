"""flash_firmware v1.3.3 的 reboot 时序修正。

v1.3.0–v1.3.2 行为由既有用例覆盖；这里只验证增量：
每次尝试内 flash_tool 必须先启动进入 USB 扫描（on_running 回调），adb
reboot 在回调里随后发出——BROM 窗口只存在于上电最初几秒，观测者必须
就位在先。真机实证见模块 docstring v1.3.3 节。
"""

from __future__ import annotations

import importlib.util
import json
import time as real_time
from pathlib import Path


_SCRIPT_DIR = (
    Path(__file__).resolve().parents[2]
    / "agent" / "scripts" / "flash_firmware" / "v1.3.3"
)

spec = importlib.util.spec_from_file_location(
    "flash_firmware_v133", _SCRIPT_DIR / "flash_firmware.py"
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
    (ver_dir := ver / "manifest.json")
    ver_dir.write_text(json.dumps({
        "family": "MLD", "version": "9.0.0.1",
        "scatter_file": "scatter.txt", "da_file": "da.bin",
    }), encoding="utf-8")
    return ver


def _wire(monkeypatch, fw_ver: Path, events: list,
          outcomes: "list | None" = None):
    """stub 全链；_run_flash_tool_with_progress 先触发 on_running 再返回成功，
    与真实现一样把 on_running 排进事件流。outcomes 按次弹出（缺省全成功）。"""
    remaining = list(outcomes or [])

    def fake_run(cmd, cwd, env, timeout, on_stage, on_percent,
                 on_running=None):
        events.append("tool-started")
        if on_running is not None:
            on_running()
        if remaining:
            return remaining.pop(0)
        return ("All command exec done", 0)

    monkeypatch.setattr(ff, "_precheck_environment",
                        lambda exe, adb, need_adb, strict:
                        (True, {"items": [], "warnings": []}))
    monkeypatch.setattr(ff, "_pick_flash_tool_exe",
                        lambda tool_dir: str(fw_ver / "flash_tool"))
    monkeypatch.setattr(ff, "_is_linux", lambda: True)
    monkeypatch.setattr(ff, "_port_for_serial",
                        lambda serial, base=ff._SYSFS_USB_BASE: "1-8")
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
    monkeypatch.setattr(ff, "_run_flash_tool_with_progress", fake_run)
    monkeypatch.setattr(ff, "_wait_device_back",
                        lambda serial, adb_path, timeout, on_tick: True)
    monkeypatch.setattr(ff, "_verify_after_flash",
                        lambda route, serial, adb, wait, on_tick:
                        (True, {"current": route.get("version")}))


def _params_env(monkeypatch, fw_ver: Path, **params):
    monkeypatch.setenv("STP_DEVICE_SERIAL", "SER1")
    monkeypatch.setenv("STP_ADB_PATH", "adb")
    monkeypatch.setenv("STP_STEP_PARAMS", json.dumps({
        "firmware_dir": str(fw_ver),
        "flash_tool_dir": str(fw_ver),
        **params,
    }))


class TestToolBeforeRebootOrdering:
    def test_reboot_fires_inside_on_running_after_tool_start(
            self, tmp_path, monkeypatch, capsys):
        """时序硬约束：tool-started 必须先于 reboot。"""
        fw_ver = _fw_dir(tmp_path)
        _params_env(monkeypatch, fw_ver, pre_reboot_wait_seconds=3)
        events: list = []
        _wire(monkeypatch, fw_ver, events)

        def fake_reboot(serial, target, adb_path, wait_seconds):
            events.append(f"reboot:{target}")
            return {"attempted": True, "target": target}

        monkeypatch.setattr(ff, "_reboot_into_flash_mode", fake_reboot)
        fake_time = _FakeTime()
        monkeypatch.setattr(ff, "time", fake_time)

        ff.main()
        payload = json.loads(capsys.readouterr().out.strip())
        assert payload["success"] is True
        assert events == ["tool-started", "reboot:normal"]
        # 提前量在工具起来之后、reboot 之前发生
        assert fake_time.sleeps == [3]
        assert payload["metrics"]["pre_reboot"]["target"] == "normal"

    def test_no_reboot_param_still_starts_tool_without_sleep(
            self, tmp_path, monkeypatch, capsys):
        fw_ver = _fw_dir(tmp_path)
        _params_env(monkeypatch, fw_ver, reboot_to_flash=False,
                    pre_reboot_wait_seconds=5)
        events: list = []
        _wire(monkeypatch, fw_ver, events)

        def unexpected(*a, **kw):
            raise AssertionError("reboot must not fire")

        monkeypatch.setattr(ff, "_reboot_into_flash_mode", unexpected)
        fake_time = _FakeTime()
        monkeypatch.setattr(ff, "time", fake_time)

        ff.main()
        payload = json.loads(capsys.readouterr().out.strip())
        assert payload["success"] is True
        assert events == ["tool-started"]
        assert fake_time.sleeps == []  # 不重启就不付提前量

    def test_retry_attempt_reboots_again_inside_callback(
            self, tmp_path, monkeypatch, capsys):
        fw_ver = _fw_dir(tmp_path)
        _params_env(monkeypatch, fw_ver, retry_backoff_seconds=0,
                    pre_reboot_wait_seconds=1)
        events: list = []
        _wire(monkeypatch, fw_ver, events, [
            ("S_FT_DOWNLOAD_FAIL blah", 1),   # attempt 1 判负
            ("All command exec done", 0),     # attempt 2 成功
        ])

        def fake_reboot(serial, target, adb_path, wait_seconds):
            events.append("reboot")
            return {"attempted": True, "target": target}

        monkeypatch.setattr(ff, "_reboot_into_flash_mode", fake_reboot)
        monkeypatch.setattr(ff, "time", _FakeTime())

        ff.main()
        payload = json.loads(capsys.readouterr().out.strip())
        assert payload["success"] is True
        assert payload["metrics"]["attempt_count"] == 2
        # 每次尝试都是 工具先起 → reboot 跟进
        assert events == ["tool-started", "reboot",
                          "tool-started", "reboot"]
