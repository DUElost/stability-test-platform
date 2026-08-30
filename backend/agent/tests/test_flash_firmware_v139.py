"""flash_firmware v1.3.9：门控保持收窄——只压制 BROM/preloader 态口。

v1.3.8 及以前由既有用例覆盖；这里验证增量：
- _gate_other_mtk 的 hold_pids 参数（BROM-only 过滤：201c 不隐藏、
  2000 隐藏）；
- _regate 用 _BROM_STAGE_PIDS（201c 不再周期 toggle）；
- main 集成：regate 期间 201c 口不动、BROM 幽灵被持续压制。
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_SCRIPT_DIR = (
    Path(__file__).resolve().parents[2]
    / "agent" / "scripts" / "flash_firmware" / "v1.3.9"
)

spec = importlib.util.spec_from_file_location(
    "flash_firmware_v139", _SCRIPT_DIR / "flash_firmware.py"
)
ff = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(ff)


def _make_sysfs(base: Path, devices: dict) -> None:
    for port, (vid, pid) in devices.items():
        dev = base / port
        dev.mkdir(parents=True, exist_ok=True)
        (dev / "idVendor").write_text(vid, encoding="utf-8")
        (dev / "idProduct").write_text(pid, encoding="utf-8")
        (dev / "authorized").write_text("1", encoding="utf-8")


class TestGateHoldPids:
    def test_brom_only_hides_2000_not_201c(self, tmp_path, monkeypatch):
        _make_sysfs(tmp_path, {
            "1-7.4.4": ("0e8d", "2000"),   # BROM 幽灵
            "1-7.3.3": ("0e8d", "201c"),   # 正常态
            "1-8": ("0e8d", "2001"),       # preloader
        })
        monkeypatch.setattr(ff, "_is_linux", lambda: True)
        monkeypatch.setattr(ff, "_sudo_available", lambda: True)
        monkeypatch.setattr(ff, "_set_authorized",
                            lambda name, val, base=ff._SYSFS_USB_BASE:
                            (True, "direct"))

        report = ff._gate_other_mtk("1-7.3.3", str(tmp_path),
                                    hold_pids=ff._BROM_STAGE_PIDS)
        # 目标 1-7.3.3 不动；1-7.4.4(2000) 与 1-8(2001) 隐藏；201c 目标本
        # 身就是 target 所以不在 others——再放一台 201c 非目标验证
        assert "1-7.4.4" in report["hidden"]
        assert "1-8" in report["hidden"]

    def test_201c_non_target_not_hidden_in_hold(self, tmp_path, monkeypatch):
        _make_sysfs(tmp_path, {
            "1-7.4.4": ("0e8d", "2000"),
            "1-7.3.3": ("0e8d", "201c"),
            "1-10": ("0e8d", "201c"),     # 非目标 201c
        })
        monkeypatch.setattr(ff, "_is_linux", lambda: True)
        monkeypatch.setattr(ff, "_set_authorized",
                            lambda name, val, base=ff._SYSFS_USB_BASE:
                            (True, "direct"))
        report = ff._gate_other_mtk("1-7.3.3", str(tmp_path),
                                    hold_pids=ff._BROM_STAGE_PIDS)
        assert "1-7.4.4" in report["hidden"]
        assert "1-10" not in report["hidden"]

    def test_full_pids_still_hides_201c(self, tmp_path, monkeypatch):
        # 初始门控（无 hold_pids）仍全量隐藏 201c
        _make_sysfs(tmp_path, {
            "1-10": ("0e8d", "201c"),
            "1-7.3.3": ("0e8d", "201c"),
        })
        monkeypatch.setattr(ff, "_is_linux", lambda: True)
        monkeypatch.setattr(ff, "_set_authorized",
                            lambda name, val, base=ff._SYSFS_USB_BASE:
                            (True, "direct"))
        report = ff._gate_other_mtk("1-7.3.3", str(tmp_path))
        assert "1-10" in report["hidden"]


class _FakeTime:
    def __init__(self) -> None:
        self.t = 100.0

    def monotonic(self) -> float:
        return self.t

    def time(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        pass


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


class TestMainRegateBromOnly:
    def test_regate_uses_brom_hold_pids(self, tmp_path, monkeypatch, capsys):
        fw_ver = _fw_dir(tmp_path)
        fake_time = _FakeTime()
        monkeypatch.setattr(ff, "time", fake_time)

        gate_kwargs: list = []

        def fake_gate(target_port, base=ff._SYSFS_USB_BASE,
                      hold_pids=None):
            gate_kwargs.append(hold_pids)
            return {"hidden": ["1-7.4.4"], "errors": {},
                    "skipped_reason": None, "target_port": target_port}

        monkeypatch.setattr(ff, "_precheck_environment",
                            lambda exe, adb, need_adb, strict:
                            (True, {"items": [], "warnings": []}))
        monkeypatch.setattr(ff, "_pick_flash_tool_exe",
                            lambda tool_dir: str(fw_ver / "flash_tool"))
        monkeypatch.setattr(ff, "_is_linux", lambda: True)
        monkeypatch.setattr(ff, "_port_for_serial",
                            lambda serial, base=ff._SYSFS_USB_BASE: "1-7.3.3")
        monkeypatch.setattr(ff, "_gate_other_mtk", fake_gate)
        monkeypatch.setattr(ff, "_restore_gated",
                            lambda gated, base=ff._SYSFS_USB_BASE:
                            {"restored": [], "errors": {}})
        monkeypatch.setattr(ff, "_acquire_host_lock",
                            lambda on_wait_tick=None: object())
        monkeypatch.setattr(ff, "_release_host_lock", lambda fd: None)
        monkeypatch.setattr(ff, "_reboot_into_flash_mode",
                            lambda serial, target, adb_path, wait_seconds:
                            {"attempted": True})

        def fake_run(cmd, cwd, env, timeout, on_stage, on_percent,
                     on_running=None):
            if on_running is not None:
                on_running()
            for pct in range(5, 100, 10):
                fake_time.t += 11.0  # 跨节流窗口
                on_percent(pct)
            return ("All command exec done", 0)

        monkeypatch.setattr(ff, "_run_flash_tool_with_progress", fake_run)
        monkeypatch.setattr(ff, "_wait_device_back",
                            lambda serial, adb_path, timeout, on_tick: True)
        monkeypatch.setattr(ff, "_verify_after_flash",
                            lambda route, serial, adb, wait, on_tick:
                            (True, {"current": route.get("version")}))
        monkeypatch.setattr(ff, "_wait_boot_stable",
                            lambda serial, adb_path, stable_seconds=20,
                            max_wait=120, on_tick=None,
                            usb_base=ff._SYSFS_USB_BASE, poll_interval=5.0:
                            {"ok": True, "boot_completed": True})

        monkeypatch.setenv("STP_DEVICE_SERIAL", "TARGETSER")
        monkeypatch.setenv("STP_ADB_PATH", "adb")
        monkeypatch.setenv("STP_STEP_PARAMS", json.dumps({
            "firmware_dir": str(fw_ver),
            "flash_tool_dir": str(fw_ver),
        }))
        ff.main()

        # 初始 gate 无 hold_pids（全量）；regate 全部 BROM-only
        assert gate_kwargs[0] is None
        assert all(kw == ff._BROM_STAGE_PIDS for kw in gate_kwargs[1:])
        assert len(gate_kwargs) >= 3
        out = capsys.readouterr().out.strip()
        payload = json.loads(out)
        assert payload["success"] is True
        assert payload["metrics"]["gating"]["regate_count"] >= 2
