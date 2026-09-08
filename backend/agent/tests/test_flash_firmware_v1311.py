"""flash_firmware v1.3.11：取消/超时后门控口必须恢复（#1025，R08-F02）。

v1.3.10 及以前由既有用例覆盖；这里验证增量：

- `_install_termination_cleanup`：SIGTERM/SIGINT/SIGHUP 到达时先调用幂等结算
  （恢复门控 + 释放 host lock），再把信号还原为默认动作并 self-signal ——
  进程仍以 WIFSIGNALED 收场，退出码语义不变；
- 结算本身抛错也不能吞掉取消（必须继续走默认动作退出）；
- main 集成：gating / host lock 就位后确实注册了处理器。
"""

from __future__ import annotations

import importlib.util
import json
import os
import signal
from pathlib import Path


_SCRIPT_DIR = (
    Path(__file__).resolve().parents[2]
    / "agent" / "scripts" / "flash_firmware" / "v1.3.11"
)

spec = importlib.util.spec_from_file_location(
    "flash_firmware_v1311", _SCRIPT_DIR / "flash_firmware.py"
)
ff = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(ff)


class TestTerminationCleanup:
    def test_handler_settles_then_reraises(self, monkeypatch):
        calls: list = []
        orig_handler = signal.getsignal(signal.SIGTERM)
        try:
            ff._install_termination_cleanup(lambda signum: calls.append(signum))
            handler = signal.getsignal(signal.SIGTERM)
            assert callable(handler) and handler is not orig_handler

            monkeypatch.setattr(
                ff.os, "kill", lambda pid, sig: calls.append(("kill", pid, sig)),
            )
            monkeypatch.setattr(
                ff.signal, "signal",
                lambda sig, action: calls.append(("dfl", sig, action)),
            )
            handler(signal.SIGTERM, None)

            assert calls[0] == int(signal.SIGTERM)  # 先结算
            assert ("kill", os.getpid(), signal.SIGTERM) in calls
            assert ("dfl", signal.SIGTERM, signal.SIG_DFL) in calls
        finally:
            signal.signal(signal.SIGTERM, orig_handler)

    def test_handler_reraises_even_if_settle_raises(self, monkeypatch):
        calls: list = []
        orig_handler = signal.getsignal(signal.SIGTERM)

        def boom(_signum):
            raise RuntimeError("settle failed")

        try:
            ff._install_termination_cleanup(boom)
            handler = signal.getsignal(signal.SIGTERM)
            monkeypatch.setattr(
                ff.os, "kill", lambda pid, sig: calls.append(("kill", sig)),
            )
            monkeypatch.setattr(ff.signal, "signal", lambda sig, action: None)
            handler(signal.SIGTERM, None)  # 不应把 RuntimeError 抛出去
            assert ("kill", signal.SIGTERM) in calls
        finally:
            signal.signal(signal.SIGTERM, orig_handler)

    def test_registers_all_three_signals(self, monkeypatch):
        registered: list = []
        monkeypatch.setattr(
            ff.signal, "signal", lambda sig, _h: registered.append(sig),
        )
        ff._install_termination_cleanup(lambda _signum: None)
        assert signal.SIGTERM in registered
        assert signal.SIGINT in registered


# ── main 集成：处理器确实挂上了 ──────────────────────────────────────────


def _fw_dir(tmp_path: Path) -> Path:
    fw = tmp_path / "firmware" / "V71"
    fw.mkdir(parents=True)
    (fw / "scatter.txt").write_text("s", encoding="utf-8")
    (fw / "da.bin").write_text("d", encoding="utf-8")
    (fw / "manifest.json").write_text(json.dumps({
        "family": "MLD", "version": "V71",
        "version_prop": "ro.build.version.incremental",
        "scatter_file": "scatter.txt", "da_file": "da.bin",
        "models": ["MLD_LX2"],
    }), encoding="utf-8")
    return fw


def _wire_happy_path(monkeypatch, fw_ver: Path) -> None:
    monkeypatch.setattr(ff, "_precheck_environment",
                        lambda exe, adb, need_adb, strict:
                        (True, {"items": [], "warnings": []}))
    monkeypatch.setattr(ff, "_pick_flash_tool_exe",
                        lambda tool_dir: str(fw_ver / "flash_tool"))
    monkeypatch.setattr(ff, "_is_linux", lambda: True)
    monkeypatch.setattr(ff, "_port_for_serial",
                        lambda serial, base=ff._SYSFS_USB_BASE: "1-7.3.3")
    monkeypatch.setattr(ff, "_gate_other_mtk",
                        lambda target_port, base=None, hold_pids=None:
                        {"hidden": ["1-5.2.4"], "errors": {},
                         "skipped_reason": None, "target_port": target_port})
    monkeypatch.setattr(ff, "_restore_gated",
                        lambda gated, base=None:
                        {"restored": (gated or {}).get("hidden", []),
                         "errors": {}})
    monkeypatch.setattr(ff, "_acquire_host_lock", lambda on_wait_tick=None: None)
    monkeypatch.setattr(ff, "_release_host_lock", lambda fd: None)
    monkeypatch.setattr(ff, "_reboot_into_flash_mode",
                        lambda serial, target, adb_path, wait_seconds:
                        {"attempted": True})
    monkeypatch.setattr(ff, "_run_flash_tool_with_progress",
                        lambda cmd, cwd, env, timeout, on_stage, on_percent,
                        on_running=None: ("All command exec done", 0))
    monkeypatch.setattr(ff, "_wait_device_back",
                        lambda serial, adb_path, timeout, on_tick: True)
    monkeypatch.setattr(ff, "_verify_after_flash",
                        lambda route, serial, adb, wait, on_tick:
                        (True, {"current": route.get("version")}))
    monkeypatch.setattr(ff, "_wait_boot_stable",
                        lambda serial, adb_path, stable_seconds=20,
                        max_wait=120, on_tick=None, usb_base=None,
                        poll_interval=5.0:
                        {"ok": True, "boot_completed": True,
                         "stable_seconds_elapsed": 20.0})


def test_main_installs_termination_cleanup(tmp_path, monkeypatch, capsys):
    fw_ver = _fw_dir(tmp_path)
    _wire_happy_path(monkeypatch, fw_ver)
    monkeypatch.setenv("STP_DEVICE_SERIAL", "TARGETSER")
    monkeypatch.setenv("STP_ADB_PATH", "adb")
    monkeypatch.setenv("STP_STEP_PARAMS", json.dumps({
        "firmware_dir": str(fw_ver),
        "flash_tool_dir": str(fw_ver),
    }))

    installed: list = []
    monkeypatch.setattr(
        ff, "_install_termination_cleanup",
        lambda settle: installed.append(settle),
    )

    ff.main()

    assert len(installed) == 1
    assert json.loads(capsys.readouterr().out.strip())["success"] is True
