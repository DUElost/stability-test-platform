"""#1591：刷机流程时序加固（v1.3.12 / v1.1.1）。"""
from __future__ import annotations
from pathlib import Path


def test_flash_v1312_model_ready_wait():
    """fingerprint 前等设备就绪（boot_completed + model 重试）。"""
    d = Path(__file__).resolve().parents[2] / "agent/scripts/flash_firmware/v1.3.12"
    src = (d / "flash_firmware.py").read_text(encoding="utf-8")
    assert "def _wait_model_ready" in src
    assert "model_ready_wait_seconds" in src
    assert "_wait_model_ready(serial, adb_path, ready_wait)" in src


def test_flash_v1312_failure_recovery():
    """flash 失败后尝试 adb reboot 恢复（避免 USB 死态）。"""
    d = Path(__file__).resolve().parents[2] / "agent/scripts/flash_firmware/v1.3.12"
    src = (d / "flash_firmware.py").read_text(encoding="utf-8")
    assert "def _attempt_device_recovery" in src
    assert "recovery = _attempt_device_recovery(serial, adb_path)" in src
    assert '"recovery": recovery' in src


def test_oobe_v111_verify_retry():
    """验证失败重试（settings 未落盘场景收敛）。"""
    d = Path(__file__).resolve().parents[2] / "agent/scripts/oobe_skip/v1.1.1"
    src = (d / "oobe_skip.py").read_text(encoding="utf-8")
    assert "verify_retries" in src
    assert "verify-retry" in src
    assert 'verify_report["retry_attempts"]' in src
