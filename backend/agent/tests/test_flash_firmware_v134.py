"""flash_firmware v1.3.4：持锁穿过核验。

场景 2 实证（2026-08-26 .66 双机并发）的竞态修复：工具退出后立即释放锁，
本机手机的看门狗重启发生在锁外，下一任的工具扫描窗会撞上可捕获态、把
新固件刷进错误的手机。v1.3.4 改为核验完成后才结算锁（幂等），异常路径
立即结算保持原兜底语义。这里断言事件时序与结算恰好一次。
"""

from __future__ import annotations

import importlib.util
import json
import time as real_time
from pathlib import Path

import pytest


_SCRIPT_DIR = (
    Path(__file__).resolve().parents[2]
    / "agent" / "scripts" / "flash_firmware" / "v1.3.4"
)

spec = importlib.util.spec_from_file_location(
    "flash_firmware_v134", _SCRIPT_DIR / "flash_firmware.py"
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


def _wire(monkeypatch, fw_ver: Path, events: list, outcomes: list):
    """stub 全链；每个桩只往 events 里追加自己的名字。"""

    def fake_run(cmd, cwd, env, timeout, on_stage, on_percent,
                 on_running=None):
        events.append("tool-started")
        if on_running is not None:
            on_running()
        if outcomes:
            return outcomes.pop(0)
        return ("All command exec done", 0)

    monkeypatch.setattr(ff, "_precheck_environment",
                        lambda exe, adb, need_adb, strict:
                        (True, {"items": [], "warnings": []}))
    monkeypatch.setattr(ff, "_pick_flash_tool_exe",
                        lambda tool_dir: str(fw_ver / "flash_tool"))
    monkeypatch.setattr(ff, "_is_linux", lambda: True)
    monkeypatch.setattr(ff, "_port_for_serial",
                        lambda serial, base=ff._SYSFS_USB_BASE: "1-6")
    monkeypatch.setattr(ff, "_gate_other_mtk",
                        lambda tp, base=ff._SYSFS_USB_BASE:
                        {"hidden": ["1-9"], "errors": {},
                         "skipped_reason": None, "target_port": tp})
    monkeypatch.setattr(ff, "_reboot_into_flash_mode",
                        lambda serial, target, adb_path, wait_seconds:
                        events.append("reboot") or
                        {"attempted": True, "target": target})
    monkeypatch.setattr(ff, "_run_flash_tool_with_progress", fake_run)
    monkeypatch.setattr(ff, "_wait_device_back",
                        lambda serial, adb_path, timeout, on_tick:
                        events.append("wait-device-back") or True)
    monkeypatch.setattr(ff, "_verify_after_flash",
                        lambda route, serial, adb, wait, on_tick:
                        events.append("verify") or
                        (True, {"current": route.get("version")}))
    monkeypatch.setattr(
        ff, "_release_host_lock",
        lambda fd: events.append("lock-released") or None)
    monkeypatch.setattr(
        ff, "_restore_gated",
        lambda gated, base=ff._SYSFS_USB_BASE:
        events.append("restore") or {"restored": [], "errors": {}})
    monkeypatch.setattr(ff, "_acquire_host_lock",
                        lambda on_wait_tick=None: object())
    fake_time = _FakeTime()
    monkeypatch.setattr(ff, "time", fake_time)
    # _release_host_lock 被 stub 后 main 内部调用的是同一 stub ✓
    monkeypatch.delenv("STP_STEP_PARAMS", raising=False)
    monkeypatch.setenv("STP_DEVICE_SERIAL", "SER1")
    monkeypatch.setenv("STP_ADB_PATH", "adb")


def _params(monkeypatch, fw_ver: Path, **params):
    monkeypatch.setenv("STP_STEP_PARAMS", json.dumps({
        "firmware_dir": str(fw_ver),
        "flash_tool_dir": str(fw_ver),
        **params,
    }))


class TestLockHeldThroughVerify:
    def test_release_only_after_verify_on_success(
            self, tmp_path, monkeypatch, capsys):
        fw_ver = _fw_dir(tmp_path)
        events: list = []
        _wire(monkeypatch, fw_ver, events, [])
        _params(monkeypatch, fw_ver)
        ff.main()
        payload = json.loads(capsys.readouterr().out.strip())
        assert payload["success"] is True
        # 核心时序：verify 先于 restore/lock-released
        assert events.index("verify") < events.index("restore")
        assert events.index("verify") < events.index("lock-released")
        # 恰好结算一次，且门控恢复在释放之前
        assert events.count("lock-released") == 1
        assert events.index("restore") < events.index("lock-released")
        assert payload["metrics"]["gating"]["restore"] == {
            "restored": [], "errors": {}}

    def test_failure_path_settles_before_output(
            self, tmp_path, monkeypatch, capsys):
        fw_ver = _fw_dir(tmp_path)
        events: list = []
        _wire(monkeypatch, fw_ver, events,
              [("S_FT_DOWNLOAD_FAIL blah", 1), ("S_FT_DOWNLOAD_FAIL x", 1)])
        _params(monkeypatch, fw_ver, retry_backoff_seconds=0)
        ff.main()
        payload = json.loads(capsys.readouterr().out.strip())
        assert payload["success"] is False
        # 失败路径也必须结算（gating.restore 进 metrics 契约）
        assert payload["metrics"]["gating"]["restore"] == {
            "restored": [], "errors": {}}
        assert events.count("lock-released") == 1

    def test_unexpected_exception_still_releases(
            self, tmp_path, monkeypatch, capsys):
        """循环内内层 try 之外的未预期异常（如 attempt>1 重画门控时）：
        立即结算后向上抛（原 finally 兜底语义）。"""
        fw_ver = _fw_dir(tmp_path)
        events: list = []
        # 首轮判负，逼出 attempt>1 的重画门控路径
        _wire(monkeypatch, fw_ver, events,
              [("S_FT_DOWNLOAD_FAIL blah", 1), ("All command exec done", 0)])

        gate_calls: list = []

        def regate_boom(tp, base=ff._SYSFS_USB_BASE):
            # 首次调用 = 循环外的初始门控，放行；第二次 = attempt>1 重画，引爆
            gate_calls.append(1)
            if len(gate_calls) >= 2:
                raise RuntimeError("sysfs vanished mid-retry")
            return {"hidden": ["1-9"], "errors": {},
                    "skipped_reason": None, "target_port": tp}

        monkeypatch.setattr(ff, "_gate_other_mtk", regate_boom)
        _params(monkeypatch, fw_ver, retry_backoff_seconds=0)
        with pytest.raises(RuntimeError):
            ff.main()
        assert "lock-released" in events
        assert events[-1] == "lock-released"
