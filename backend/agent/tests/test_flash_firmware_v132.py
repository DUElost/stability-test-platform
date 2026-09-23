"""flash_firmware v1.3.2 的 reboot 语义修正。

v1.3.0/v1.3.1 行为由既有用例覆盖；这里只验证增量：
默认（缺省 / "" / "normal"）发不带 target 的普通 `adb reboot`——完整上电
流经 BROM 窗口，等待中的 flash_tool 才能抓中；"bootloader"/"fastboot"
为显式热重启选项（v1.3.2 真机实证：bootloader 直达 fastboot 态跳过 BROM）。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


_SCRIPT_DIR = (
    Path(__file__).resolve().parents[2]
    / "agent" / "scripts" / "flash_firmware"
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


