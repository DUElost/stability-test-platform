"""#1591：刷机流程时序加固（flash_firmware v1.3.14+ / oobe_skip v1.1.2）。"""
from __future__ import annotations

import importlib.util
import json
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


def _load_flash_v1315():
    d = Path(__file__).resolve().parents[2] / "agent/scripts/flash_firmware/v1.3.15"
    spec = importlib.util.spec_from_file_location(
        "flash_firmware_v1315", d / "flash_firmware.py",
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_flash_v1315_orphan_gate_restore(tmp_path, monkeypatch):
    """#1591：启动前恢复上次 SIGKILL 留下的门控口。"""
    mod = _load_flash_v1315()
    state = tmp_path / "gated.json"
    sysfs = tmp_path / "sys"
    port = "1-1"
    auth = sysfs / port / "authorized"
    auth.parent.mkdir(parents=True)
    auth.write_text("0")
    state.write_text(json.dumps({"hidden": [port], "pid": 1}), encoding="utf-8")

    monkeypatch.setattr(mod, "_GATE_STATE_PATH", str(state))
    report = mod._restore_orphaned_gates(base=str(sysfs), path=str(state))
    assert port in report["restored"]
    assert auth.read_text().strip() == "1"
    assert not state.exists()


def test_flash_v1315_persist_and_clear(tmp_path, monkeypatch):
    mod = _load_flash_v1315()
    state = tmp_path / "gated.json"
    monkeypatch.setattr(mod, "_GATE_STATE_PATH", str(state))
    mod._persist_gated_ports(["2-1", "2-2"], path=str(state))
    data = json.loads(state.read_text(encoding="utf-8"))
    assert data["hidden"] == ["2-1", "2-2"]
    mod._clear_gated_state(path=str(state))
    assert not state.exists()


def test_pipeline_flash_terminate_grace():
    """#1591：flash_firmware 取消宽限加长，给 settle 留时间。"""
    from backend.agent.pipeline_engine import _script_terminate_grace_seconds

    assert _script_terminate_grace_seconds(
        "/opt/agent/scripts/flash_firmware/v1.3.15/flash_firmware.py",
    ) == 8.0
    assert _script_terminate_grace_seconds(
        "/opt/agent/scripts/oobe_skip/v1.1.2/oobe_skip.py",
    ) == 2.0
