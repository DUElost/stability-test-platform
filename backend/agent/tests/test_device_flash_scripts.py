"""#1591：刷机流程时序加固（flash_firmware v1.3.14 / oobe_skip v1.1.2）。"""
from __future__ import annotations
from pathlib import Path


def test_flash_v1314_model_ready_wait():
    """fingerprint 前等设备就绪（model 轮询）。"""
    d = Path(__file__).resolve().parents[2] / "agent/scripts/flash_firmware/v1.3.14"
    src = (d / "flash_firmware.py").read_text(encoding="utf-8")
    assert "def _wait_model_ready" in src
    assert "model_ready_wait_seconds" in src
    assert "_wait_model_ready(serial, adb_path, ready_wait)" in src


def test_flash_v1314_failure_recovery():
    """flash 失败后尝试 adb reboot 恢复（避免 USB 死态）。"""
    d = Path(__file__).resolve().parents[2] / "agent/scripts/flash_firmware/v1.3.14"
    src = (d / "flash_firmware.py").read_text(encoding="utf-8")
    assert "def _attempt_device_recovery" in src
    assert "recovery = _attempt_device_recovery(serial, adb_path)" in src
    assert '"recovery": recovery' in src
    # 不得引用未定义别名（初版误用 subprocess_run）
    assert "subprocess_run(" not in src
    assert "subprocess.run(" in src


def test_oobe_v112_verify_retry():
    """验证失败重试（settings 未落盘场景收敛）。"""
    d = Path(__file__).resolve().parents[2] / "agent/scripts/oobe_skip/v1.1.2"
    src = (d / "oobe_skip.py").read_text(encoding="utf-8")
    assert "verify_retries" in src
    assert "verify-retry" in src
    assert 'verify_report["retry_attempts"]' in src


def test_published_versions_untouched():
    """已发布 v1.3.12 / v1.1.1 不含 #1591 新行为（ADR-0020）。"""
    root = Path(__file__).resolve().parents[2] / "agent/scripts"
    old_flash = (root / "flash_firmware/v1.3.12/flash_firmware.py").read_text(
        encoding="utf-8")
    assert "def _wait_model_ready" not in old_flash
    assert "def _attempt_device_recovery" not in old_flash
    old_oobe = (root / "oobe_skip/v1.1.1/oobe_skip.py").read_text(encoding="utf-8")
    assert "verify_retries" not in old_oobe
    assert "verify-retry" not in old_oobe
