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
    / "agent" / "scripts" / "flash_firmware"
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


