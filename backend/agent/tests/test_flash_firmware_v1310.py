"""flash_firmware v1.3.10：boot 稳定指纹收窄——只取 MTK 口。

v1.3.9 及以前由既有用例覆盖；这里验证增量：
- _usb_topology_fingerprint：只取 MTK 口（vid=0e8d）——非 MTK 的 USB 抖动
  不得进入指纹，否则稳定窗口会被无关事件无限重置（守卫退化成白等）；
  设备目录枚举/跳过规则/reboot 变化检测；
- _wait_boot_stable：boot_completed=1 + 拓扑稳定窗口 → ok；MTK 拓扑变化
  重置窗口；非 MTK 抖动不重置；首轮即记基线（不空烧一个 poll 周期）；
  超时与 adb 不可达 → ok=False（不判失败，按「确认卡死」放行）；
- main 集成：verify 通过后、settle 前调用 _wait_boot_stable，metrics 含
  boot_stable；`done` PROGRESS 在 boot-stabilize 之后。
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


_SCRIPT_DIR = (
    Path(__file__).resolve().parents[2]
    / "agent" / "scripts" / "flash_firmware" / "v1.3.10"
)

spec = importlib.util.spec_from_file_location(
    "flash_firmware_v1310", _SCRIPT_DIR / "flash_firmware.py"
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
            "1-5.2.3": ("1234", "5678"),  # 非 MTK → 不进指纹
        })
        (tmp_path / "usb1").mkdir()
        (tmp_path / "1-2:1.0").mkdir()
        fp = ff._usb_topology_fingerprint(str(tmp_path))
        assert fp == "1-2:2000|1-7.3.3:2046"
        assert "1-5.2.3" not in fp
        assert "usb1" not in fp
        assert "1-2:1.0" not in fp
        assert fp == ff._usb_topology_fingerprint(str(tmp_path))

    def test_reboot_changes_fingerprint(self, tmp_path):
        _make_sysfs(tmp_path, {"1-7.3.3": ("0e8d", "2046")})
        before = ff._usb_topology_fingerprint(str(tmp_path))
        (tmp_path / "1-7.3.3" / "idProduct").write_text("2000", encoding="utf-8")
        after = ff._usb_topology_fingerprint(str(tmp_path))
        assert before != after

    def test_non_mtk_churn_never_enters_fingerprint(self, tmp_path):
        _make_sysfs(tmp_path, {
            "1-7.3.3": ("0e8d", "2046"),
            "1-5.2.3": ("1234", "0001"),
        })
        before = ff._usb_topology_fingerprint(str(tmp_path))
        (tmp_path / "1-5.2.3" / "idProduct").write_text("0002", encoding="utf-8")
        _make_sysfs(tmp_path, {"1-5.2.4": ("1234", "0003")})
        assert ff._usb_topology_fingerprint(str(tmp_path)) == before

    def test_missing_base_empty(self, tmp_path):
        assert ff._usb_topology_fingerprint(str(tmp_path / "nope")) == ""

    def test_missing_vendor_excluded(self, tmp_path):
        (tmp_path / "1-2").mkdir()
        assert ff._usb_topology_fingerprint(str(tmp_path)) == ""

    def test_mtk_port_missing_pid_uses_empty(self, tmp_path):
        dev = tmp_path / "1-2"
        dev.mkdir()
        (dev / "idVendor").write_text("0e8d", encoding="utf-8")
        assert ff._usb_topology_fingerprint(str(tmp_path)) == "1-2:"


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

    def test_mtk_topology_change_resets_stable_window(
            self, tmp_path, monkeypatch):
        self._make_usb(tmp_path, pid="2046")
        monkeypatch.setattr(ff, "_adb_getprop",
                            lambda prop, adb, serial, timeout=10: "1")

        calls = {"n": 0}
        orig_fp = ff._usb_topology_fingerprint
        pid_file = tmp_path / "1-7.3.3" / "idProduct"

        def rebooting_fingerprint(base=ff._SYSFS_USB_BASE):
            calls["n"] += 1
            pid_file.write_text("200%d" % (calls["n"] % 2), encoding="utf-8")
            return orig_fp(base)

        monkeypatch.setattr(ff, "_usb_topology_fingerprint", rebooting_fingerprint)
        report = ff._wait_boot_stable(
            "S1", "adb", stable_seconds=0.1, max_wait=0.5,
            usb_base=str(tmp_path), poll_interval=0.05)
        assert report["ok"] is False
        assert report["boot_completed"] is True

    def test_non_mtk_churn_does_not_reset_stable_window(
            self, tmp_path, monkeypatch):
        self._make_usb(tmp_path, pid="2046")
        monkeypatch.setattr(ff, "_adb_getprop",
                            lambda prop, adb, serial, timeout=10: "1")

        noise = tmp_path / "1-5.2.3"
        noise.mkdir()
        (noise / "idVendor").write_text("1234", encoding="utf-8")
        (noise / "idProduct").write_text("0001", encoding="utf-8")

        calls = {"n": 0}
        orig_fp = ff._usb_topology_fingerprint

        def churning_fingerprint(base=ff._SYSFS_USB_BASE):
            calls["n"] += 1
            (noise / "idProduct").write_text(str(calls["n"]), encoding="utf-8")
            return orig_fp(base)

        monkeypatch.setattr(ff, "_usb_topology_fingerprint", churning_fingerprint)
        report = ff._wait_boot_stable(
            "S1", "adb", stable_seconds=0.1, max_wait=2,
            usb_base=str(tmp_path), poll_interval=0.05)
        assert report["ok"] is True

    def test_first_poll_starts_window_without_wasting_a_cycle(
            self, tmp_path, monkeypatch):
        self._make_usb(tmp_path, pid="2046")
        calls = {"n": 0}

        def counting_getprop(prop, adb, serial, timeout=10):
            calls["n"] += 1
            return "1"

        monkeypatch.setattr(ff, "_adb_getprop", counting_getprop)
        report = ff._wait_boot_stable(
            "S1", "adb", stable_seconds=0.2, max_wait=5,
            usb_base=str(tmp_path), poll_interval=0.05)
        assert report["ok"] is True
        assert calls["n"] <= 6

    def test_adb_unreachable_times_out_ok_false(self, tmp_path, monkeypatch):
        self._make_usb(tmp_path)
        monkeypatch.setattr(ff, "_adb_getprop",
                            lambda prop, adb, serial, timeout=10: None)
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
    events: list = []

    def fake_gate(target_port, base=ff._SYSFS_USB_BASE, hold_pids=None):
        return {"hidden": ["1-5.2.4"], "errors": {},
                "skipped_reason": None, "target_port": target_port}

    def fake_restore(gated, base=ff._SYSFS_USB_BASE):
        return {"restored": (gated or {}).get("hidden", []), "errors": {}}

    lock_token = object()
    progress_stages: list[str] = []
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
        if on_tick is not None:
            on_tick()
        return {"ok": boot_stable_ok, "boot_completed": True,
                "stable_seconds_elapsed": 20.0}

    orig_emit = ff._emit_progress

    def tracking_emit(seq, **kwargs):
        if "stage" in kwargs:
            progress_stages.append(kwargs["stage"])
        return orig_emit(seq, **kwargs)

    monkeypatch.setattr(ff, "_wait_boot_stable", fake_boot_stable)
    monkeypatch.setattr(ff, "_emit_progress", tracking_emit)
    return events, progress_stages


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
        events, _ = _wire_happy_path(monkeypatch, fw_ver, boot_stable_ok=True)
        _params_env(monkeypatch, fw_ver)
        ff.main()
        assert events == ["boot-stable"]
        out = capsys.readouterr().out.strip()
        payload = json.loads(out)
        assert payload["success"] is True
        assert payload["metrics"]["boot_stable"]["ok"] is True

    def test_done_progress_after_boot_stabilize(
            self, tmp_path, monkeypatch, capsys):
        fw_ver = _fw_dir(tmp_path)
        _, stages = _wire_happy_path(monkeypatch, fw_ver, boot_stable_ok=True)
        _params_env(monkeypatch, fw_ver)
        ff.main()
        assert "boot-stabilize" in stages
        assert stages.index("done") > stages.index("boot-stabilize")

    def test_boot_stable_timeout_still_success(
            self, tmp_path, monkeypatch, capsys):
        fw_ver = _fw_dir(tmp_path)
        events, _ = _wire_happy_path(monkeypatch, fw_ver, boot_stable_ok=False)
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
        events, _ = _wire_happy_path(monkeypatch, fw_ver, boot_stable_ok=True)
        monkeypatch.setattr(ff, "_verify_after_flash",
                            lambda route, serial, adb, wait, on_tick:
                            (False, {"error": "mismatch"}))
        _params_env(monkeypatch, fw_ver)
        ff.main()
        assert events == []
        out = capsys.readouterr().out.strip()
        payload = json.loads(out)
        assert payload["success"] is False
        assert "mismatch" in payload["error_message"]
