"""flash_firmware v1.3.2 的 reboot 语义修正。

v1.3.0/v1.3.1 行为由既有用例覆盖；这里只验证增量：
默认（缺省 / "" / "normal"）发不带 target 的普通 `adb reboot`——完整上电
流经 BROM 窗口，等待中的 flash_tool 才能抓中；"bootloader"/"fastboot"
为显式热重启选项（v1.3.2 真机实证：bootloader 直达 fastboot 态跳过 BROM）。
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


_SCRIPT_DIR = (
    Path(__file__).resolve().parents[2]
    / "agent" / "scripts" / "flash_firmware" / "v1.3.2"
)

spec = importlib.util.spec_from_file_location(
    "flash_firmware_v132", _SCRIPT_DIR / "flash_firmware.py"
)
ff = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(ff)


def _capture_reboot(monkeypatch, target: str, pre_state: str = "device"):
    """调 _reboot_into_flash_mode，捕获发给 adb 的 argv。"""
    captured: list = []

    class _Proc:
        returncode = 0
        stderr = ""

    monkeypatch.setattr(
        ff, "_adb_device_state", lambda serial, adb_path: pre_state)
    monkeypatch.setattr(ff.subprocess, "run",
                        lambda argv, **kw: captured.append(argv) or _Proc())
    report = ff._reboot_into_flash_mode(
        serial="SER1", target=target, adb_path="adb", wait_seconds=0)
    return report, captured


class TestRebootTargetSemantics:
    @pytest.mark.parametrize("target", ["normal", ""])
    def test_normal_reboot_has_no_target_arg(self, monkeypatch, target):
        """"normal" / 空串 → 普通 adb reboot，不带模式参数。"""
        report, captured = _capture_reboot(monkeypatch, target)
        assert captured[0] == ["adb", "-s", "SER1", "reboot"]
        assert report["attempted"] is True
        assert report["target"] == "normal"

    @pytest.mark.parametrize("raw,effective", [
        ("bootloader", "bootloader"), ("BOOTLOADER", "bootloader"),
        ("fastboot", "fastboot"),
    ])
    def test_explicit_targets_pass_through(
            self, monkeypatch, raw, effective):
        report, captured = _capture_reboot(monkeypatch, raw)
        assert captured[0] == ["adb", "-s", "SER1", "reboot", effective]
        assert report["target"] == effective

    def test_device_not_ready_skips_reboot(self, monkeypatch):
        report, captured = _capture_reboot(monkeypatch, "normal",
                                           pre_state="no-device")
        assert report["attempted"] is False
        assert "not ready" in report["skip_reason"]
        assert captured == []


class TestMainWiringDefaultNormal:
    def test_default_params_issue_plain_reboot(
            self, tmp_path, monkeypatch, capsys):
        """main() 缺省参数下发出的必须是普通 reboot（真机实证的关键行为）。"""
        ver_dir = tmp_path / "fw" / "MLD" / "9.9.9.9"
        ver_dir.mkdir(parents=True)
        (ver_dir / "scatter.txt").write_text("s", encoding="utf-8")
        (ver_dir / "da.bin").write_text("d", encoding="utf-8")
        (ver_dir / "manifest.json").write_text(json.dumps({
            "family": "MLD", "version": "9.9.9.9",
            "scatter_file": "scatter.txt", "da_file": "da.bin",
        }), encoding="utf-8")

        monkeypatch.setenv("STP_DEVICE_SERIAL", "SER1")
        # 显式 firmware_dir：路由不碰真实 adb；版本比对目标取 manifest。
        # flash_tool_dir 同理必须显式：git 外的部署目录 CI 检出没有
        monkeypatch.setenv("STP_STEP_PARAMS", json.dumps({
            "firmware_dir": str(ver_dir),
            "flash_tool_dir": str(ver_dir),
        }))
        seen: list = []
        monkeypatch.setattr(ff, "_precheck_environment",
                            lambda exe, adb, need_adb, strict:
                            (True, {"items": [], "warnings": []}))
        monkeypatch.setattr(ff, "_is_linux", lambda: True)
        monkeypatch.setattr(ff, "_gate_other_mtk",
                            lambda tp, base=ff._SYSFS_USB_BASE:
                            {"hidden": [], "errors": {},
                             "skipped_reason": "none visible", "target_port": tp})
        monkeypatch.setattr(ff, "_restore_gated",
                            lambda gated, base=ff._SYSFS_USB_BASE:
                            {"restored": [], "errors": {}})
        monkeypatch.setattr(ff, "_acquire_host_lock",
                            lambda on_wait_tick=None: object())
        monkeypatch.setattr(ff, "_release_host_lock", lambda fd: None)

        def fake_reboot(serial, target, adb_path, wait_seconds):
            seen.append(target)
            return {"attempted": True, "target": target}

        monkeypatch.setattr(ff, "_reboot_into_flash_mode", fake_reboot)
        monkeypatch.setattr(ff, "_pick_flash_tool_exe",
                            lambda tool_dir: str(ver_dir / "flash_tool"))
        monkeypatch.setattr(ff, "_run_flash_tool_with_progress",
                            lambda cmd, cwd, env, timeout, on_stage,
                            on_percent: ("All command exec done", 0))
        monkeypatch.setattr(ff, "_wait_device_back",
                            lambda serial, adb_path, timeout, on_tick: True)
        monkeypatch.setattr(ff, "_verify_after_flash",
                            lambda route, serial, adb, wait, on_tick:
                            (True, {"current": route.get("version")}))

        ff.main()
        payload = json.loads(capsys.readouterr().out.strip())
        assert payload["success"] is True
        assert seen == ["normal"]  # 缺省即普通重启
        assert payload["metrics"]["pre_reboot"]["target"] == "normal"
