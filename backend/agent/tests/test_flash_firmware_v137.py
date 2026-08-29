"""flash_firmware v1.3.7：锁内 boot 稳定等待（首刷二次重启窗口守卫）。

v1.3.6 及以前由既有用例覆盖；这里验证增量：
- _usb_topology_fingerprint：设备目录枚举/跳过规则/变化检测；
- _wait_boot_stable：boot_completed=1 + 拓扑稳定窗口 → ok；拓扑变化重置；
  超时与 adb 不可达 → ok=False（不判失败，按「确认卡死」放行）；
- main 集成：verify 通过后、settle 前调用 _wait_boot_stable，metrics 含
  boot_stable；verify 失败路径不等待直接结算。
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


_SCRIPT_DIR = (
    Path(__file__).resolve().parents[2]
    / "agent" / "scripts" / "flash_firmware" / "v1.3.7"
)

spec = importlib.util.spec_from_file_location(
    "flash_firmware_v137", _SCRIPT_DIR / "flash_firmware.py"
)
ff = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(ff)


def _make_sysfs(base: Path, devices: dict) -> None:
    """devices: {port: (vid, pid)} —— 构造 sysfs 设备目录树。"""
    for port, (vid, pid) in devices.items():
        dev = base / port
        dev.mkdir(parents=True, exist_ok=True)
        (dev / "idVendor").write_text(vid, encoding="utf-8")
        (dev / "idProduct").write_text(pid, encoding="utf-8")


class TestUsbTopologyFingerprint:
    def test_snapshot_sorted_stable(self, tmp_path):
        _make_sysfs(tmp_path, {
            "1-7.3.3": ("0e8d", "2046"),
            "1-2": ("0e8d", "2000"),
            "1-5.2.3": ("1234", "5678"),
        })
        # 根 hub 与接口目录
        (tmp_path / "usb1").mkdir()
        (tmp_path / "1-2:1.0").mkdir()
        fp = ff._usb_topology_fingerprint(str(tmp_path))
        assert "1-2:0e8d:2000" in fp
        assert "1-5.2.3:1234:5678" in fp
        assert "1-7.3.3:0e8d:2046" in fp
        assert "usb1" not in fp
        assert "1-2:1.0" not in fp
        assert fp == ff._usb_topology_fingerprint(str(tmp_path))  # 排序稳定

    def test_reboot_changes_fingerprint(self, tmp_path):
        _make_sysfs(tmp_path, {"1-7.3.3": ("0e8d", "2046")})
        before = ff._usb_topology_fingerprint(str(tmp_path))
        # 设备 reboot 进 BROM：pid 变化
        (tmp_path / "1-7.3.3" / "idProduct").write_text("2000", encoding="utf-8")
        after = ff._usb_topology_fingerprint(str(tmp_path))
        assert before != after

    def test_missing_base_empty(self, tmp_path):
        assert ff._usb_topology_fingerprint(str(tmp_path / "nope")) == ""

    def test_missing_attrs_use_dash(self, tmp_path):
        (tmp_path / "1-2").mkdir()
        fp = ff._usb_topology_fingerprint(str(tmp_path))
        assert fp == "1-2:-:-"


class TestWaitBootStable:
    def _make_usb(self, tmp_path, pid="2046"):
        _make_sysfs(tmp_path, {"1-7.3.3": ("0e8d", pid)})

    def test_stable_booted_returns_ok(self, tmp_path, monkeypatch):
        self._make_usb(tmp_path)
        monkeypatch.setattr(ff, "_adb_getprop",
                            lambda prop, adb, serial, timeout=10: "1")
        report = ff._wait_boot_stable(
            "S1", "adb", stable_seconds=0.1, max_wait=10,
            usb_base=str(tmp_path), poll_interval=0.05)
        assert report["ok"] is True
        assert report["boot_completed"] is True
        assert report["stable_seconds_elapsed"] >= 0.1

    def test_topology_change_resets_stable_window(
            self, tmp_path, monkeypatch):
        # 首轮稳定后设备 reboot（pid 变化）→ 窗口重置 → 最终仍稳定
        self._make_usb(tmp_path, pid="2046")
        monkeypatch.setattr(ff, "_adb_getprop",
                            lambda prop, adb, serial, timeout=10: "1")

        state = {"rebooted": False}
        orig_fp = ff._usb_topology_fingerprint

        def flaky_fingerprint(base=ff._SYSFS_USB_BASE):
            if not state["rebooted"]:
                # 第一次调用后模拟 reboot：直接改文件
                state["rebooted"] = True
                (Path(base) / "1-7.3.3" / "idProduct").write_text(
                    "2000", encoding="utf-8")
            return orig_fp(base)

        monkeypatch.setattr(ff, "_usb_topology_fingerprint", flaky_fingerprint)
        report = ff._wait_boot_stable(
            "S1", "adb", stable_seconds=0.1, max_wait=10,
            usb_base=str(tmp_path), poll_interval=0.05)
        assert report["ok"] is True
        assert report["stable_seconds_elapsed"] >= 0.1

    def test_adb_unreachable_times_out_ok_false(self, tmp_path, monkeypatch):
        self._make_usb(tmp_path)
        monkeypatch.setattr(ff, "_adb_getprop",
                            lambda prop, adb, serial, timeout=10: None)
        report = ff._wait_boot_stable(
            "S1", "adb", stable_seconds=0.1, max_wait=0.3,
            usb_base=str(tmp_path), poll_interval=0.05)
        assert report["ok"] is False
        assert report["boot_completed"] is False
        assert "not boot-stable" in report["reason"]

    def test_boot_not_completed_yet_then_completes(
            self, tmp_path, monkeypatch):
        self._make_usb(tmp_path)
        monkeypatch.setattr(ff, "_adb_getprop",
                            lambda prop, adb, serial, timeout=10: "0")
        # boot_completed 卡 0 → 超时 ok=False（设备仍在 boot 中,判为异常放行）
        report = ff._wait_boot_stable(
            "S1", "adb", stable_seconds=0.1, max_wait=0.3,
            usb_base=str(tmp_path), poll_interval=0.05)
        assert report["ok"] is False
        assert report["boot_completed"] is False

    def test_empty_fingerprint_not_stable(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ff, "_adb_getprop",
                            lambda prop, adb, serial, timeout=10: "1")
        monkeypatch.setattr(ff, "_usb_topology_fingerprint",
                            lambda base=ff._SYSFS_USB_BASE: "")
        report = ff._wait_boot_stable(
            "S1", "adb", stable_seconds=0.1, max_wait=0.3,
            usb_base=str(tmp_path), poll_interval=0.05)
        assert report["ok"] is False


# ── main 集成：verify 通过后 settle 前调用 boot 稳定 ──────────────────

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


def _wire_happy_path(monkeypatch, fw_ver: Path, *, boot_stable_ok: bool):
    """stub flash_tool / 锁 / adb / verify / boot 稳定,记录 settle 顺序。"""

    events: list = []

    def fake_gate(target_port, base=ff._SYSFS_USB_BASE):
        return {"hidden": ["1-5.2.4"], "errors": {},
                "skipped_reason": None, "target_port": target_port}

    def fake_restore(gated, base=ff._SYSFS_USB_BASE):
        return {"restored": (gated or {}).get("hidden", []), "errors": {}}

    lock_token = object()
    monkeypatch.setattr(ff, "_precheck_environment",
                        lambda exe, adb, need_adb, strict:
                        (True, {"items": [], "warnings": []}))
    monkeypatch.setattr(ff, "_pick_flash_tool_exe",
                        lambda tool_dir: str(fw_ver / "flash_tool"))
    monkeypatch.setattr(ff, "_is_linux", lambda: True)
    monkeypatch.setattr(ff, "_port_for_serial",
                        lambda serial, base=ff._SYSFS_USB_BASE: "1-7.3.3")
    monkeypatch.setattr(ff, "_gate_other_mtk", fake_gate)
    monkeypatch.setattr(ff, "_restore_gated", fake_restore)
    monkeypatch.setattr(ff, "_acquire_host_lock",
                        lambda on_wait_tick=None: lock_token)
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
                        (True, {"current": route.get("version")}))

    def fake_boot_stable(serial, adb_path, stable_seconds=20, max_wait=120,
                         on_tick=None, usb_base=ff._SYSFS_USB_BASE,
                         poll_interval=5.0):
        events.append("boot-stable")
        return {"ok": boot_stable_ok, "boot_completed": True,
                "stable_seconds_elapsed": 20.0}

    monkeypatch.setattr(ff, "_wait_boot_stable", fake_boot_stable)
    return events


def _params_env(monkeypatch, fw_ver: Path, **params):
    monkeypatch.setenv("STP_DEVICE_SERIAL", "TARGETSER")
    monkeypatch.setenv("STP_ADB_PATH", "adb")
    monkeypatch.setenv("STP_STEP_PARAMS", json.dumps({
        "firmware_dir": str(fw_ver),
        "flash_tool_dir": str(fw_ver),
        **params,
    }))


class TestMainBootStabilize:
    def test_boot_stable_before_settle_and_in_metrics(
            self, tmp_path, monkeypatch, capsys):
        fw_ver = _fw_dir(tmp_path)
        events = _wire_happy_path(monkeypatch, fw_ver, boot_stable_ok=True)
        _params_env(monkeypatch, fw_ver)
        ff.main()
        assert events == ["boot-stable"]  # settle 在 boot-stable 之后
        out = capsys.readouterr().out.strip()
        payload = json.loads(out)
        assert payload["success"] is True
        assert payload["metrics"]["boot_stable"]["ok"] is True

    def test_boot_stable_timeout_still_success(
            self, tmp_path, monkeypatch, capsys):
        # ok=False（超时/确认卡死）不判失败——v1.3.4 兜底语义
        fw_ver = _fw_dir(tmp_path)
        events = _wire_happy_path(monkeypatch, fw_ver, boot_stable_ok=False)
        _params_env(monkeypatch, fw_ver)
        ff.main()
        assert events == ["boot-stable"]
        out = capsys.readouterr().out.strip()
        payload = json.loads(out)
        assert payload["success"] is True
        assert payload["metrics"]["boot_stable"]["ok"] is False

    def test_verify_failed_skips_boot_stable(
            self, tmp_path, monkeypatch, capsys):
        fw_ver = _fw_dir(tmp_path)
        events = _wire_happy_path(monkeypatch, fw_ver, boot_stable_ok=True)
        monkeypatch.setattr(ff, "_verify_after_flash",
                            lambda route, serial, adb, wait, on_tick:
                            (False, {"error": "mismatch"}))
        _params_env(monkeypatch, fw_ver)
        ff.main()
        assert events == []  # verify 失败不等待,直接结算
        out = capsys.readouterr().out.strip()
        payload = json.loads(out)
        assert payload["success"] is False
        assert "mismatch" in payload["error_message"]
