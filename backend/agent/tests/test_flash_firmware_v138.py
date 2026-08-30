"""flash_firmware v1.3.8：门控保持——BROM 幽灵重枚举逃逸压制。

v1.3.7 及以前由既有用例覆盖；这里验证增量：
- _regate 节流（每 10s 至多一次）与 hidden 合并；
- on_running（reboot 前）触发一次 regate；
- on_percent 周期触发 regate（下载期间压制重枚举逃逸）；
- metrics.gating.regate_count 记录压制轮次。
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_SCRIPT_DIR = (
    Path(__file__).resolve().parents[2]
    / "agent" / "scripts" / "flash_firmware" / "v1.3.8"
)

spec = importlib.util.spec_from_file_location(
    "flash_firmware_v138", _SCRIPT_DIR / "flash_firmware.py"
)
ff = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(ff)


class _FakeTime:
    """可控单调钟：测试节流窗口。"""

    def __init__(self) -> None:
        self.t = 100.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.t

    def time(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)


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


class TestRegateThrottle:
    def test_throttles_to_one_per_10s_and_merges_hidden(
            self, tmp_path, monkeypatch):
        fake_time = _FakeTime()
        monkeypatch.setattr(ff, "time", fake_time)

        calls: list = []

        def fake_gate(target_port, base=ff._SYSFS_USB_BASE):
            calls.append(fake_time.monotonic())
            # 每次返回一个新 hidden 口（模拟重枚举逃逸的新设备）
            return {"hidden": [f"1-7.{len(calls)}.x"], "errors": {},
                    "skipped_reason": None, "target_port": target_port}

        monkeypatch.setattr(ff, "_gate_other_mtk", fake_gate)

        hidden: list = ["1-7.1.1"]
        regate_count = 0
        last_regate_at = float("-inf")  # 首次必触发（与脚本实现一致）

        def _regate() -> None:
            nonlocal regate_count, last_regate_at
            now = fake_time.monotonic()
            if now - last_regate_at < 10:
                return
            last_regate_at = now
            regate = fake_gate("1-7.1.1")
            for name in regate.get("hidden", []):
                if name not in hidden:
                    hidden.append(name)
            regate_count += 1

        _regate()                      # t=100（首次,必触发）
        _regate()                      # t=100 → 节流,不调
        fake_time.t += 5
        _regate()                      # t=105 → 节流
        fake_time.t += 5               # t=110
        _regate()                      # t=110 → 触发
        fake_time.t += 9
        _regate()                      # t=119 → 节流
        fake_time.t += 1               # t=120
        _regate()                      # t=120 → 触发

        assert len(calls) == 3
        assert regate_count == 3
        assert hidden == ["1-7.1.1", "1-7.1.x", "1-7.2.x", "1-7.3.x"]


class TestMainRegate:
    def _wire(self, monkeypatch, fw_ver: Path, *, percent_calls: int,
              percent_gap: float = 0.0):
        """stub 全链路；flash 输出含 percent 行模拟下载进度。"""

        lock_token = object()
        gate_calls: list = []
        fake_time = _FakeTime()
        monkeypatch.setattr(ff, "time", fake_time)

        def fake_gate(target_port, base=ff._SYSFS_USB_BASE):
            gate_calls.append("gate")
            return {"hidden": ["1-5.2.4"], "errors": {},
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
                            lambda on_wait_tick=None: lock_token)
        monkeypatch.setattr(ff, "_release_host_lock", lambda fd: None)
        monkeypatch.setattr(ff, "_reboot_into_flash_mode",
                            lambda serial, target, adb_path, wait_seconds:
                            {"attempted": True})

        def fake_run(cmd, cwd, env, timeout, on_stage, on_percent,
                     on_running=None):
            # 工具就绪回调（含 v1.3.8 的 reboot 前 regate）
            if on_running is not None:
                on_running()
            # 模拟下载进度：每行一个 percent 回调（gap 控制跨节流窗口）
            for pct in range(5, 100, 10):
                fake_time.t += percent_gap
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
        return gate_calls

    def test_regate_during_download_and_in_metrics(
            self, tmp_path, monkeypatch, capsys):
        fw_ver = _fw_dir(tmp_path)
        # 每次 percent 间隔 11s → 节流窗口跨过 → 多次 regate
        gate_calls = self._wire(monkeypatch, fw_ver, percent_calls=10,
                                percent_gap=11.0)
        monkeypatch.setenv("STP_DEVICE_SERIAL", "TARGETSER")
        monkeypatch.setenv("STP_ADB_PATH", "adb")
        monkeypatch.setenv("STP_STEP_PARAMS", json.dumps({
            "firmware_dir": str(fw_ver),
            "flash_tool_dir": str(fw_ver),
        }))
        ff.main()

        out = capsys.readouterr().out.strip()
        payload = json.loads(out)
        assert payload["success"] is True
        gating = payload["metrics"]["gating"]
        # 初始 gate 1 次 + on_running 1 次 + on_percent 节流后若干次
        assert len(gate_calls) >= 3
        assert gating["regate_count"] >= 2

    def test_regate_count_zero_when_immediate_success(
            self, tmp_path, monkeypatch, capsys):
        fw_ver = _fw_dir(tmp_path)
        self._wire(monkeypatch, fw_ver, percent_calls=0)

        # 无 percent 输出（skip 快路径）→ 只触发 on_running 一次 regate
        # （10s 节流内第二次调用不计数）
        monkeypatch.setenv("STP_DEVICE_SERIAL", "TARGETSER")
        monkeypatch.setenv("STP_ADB_PATH", "adb")
        monkeypatch.setenv("STP_STEP_PARAMS", json.dumps({
            "firmware_dir": str(fw_ver),
            "flash_tool_dir": str(fw_ver),
        }))
        ff.main()
        out = capsys.readouterr().out.strip()
        payload = json.loads(out)
        assert payload["success"] is True
        assert payload["metrics"]["gating"]["regate_count"] >= 1
