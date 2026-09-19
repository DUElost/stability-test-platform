"""monkey_setup v2.3.8：att_clean 的 force-stop 超时降级（#2777）。

缺陷（.59 六窗复发，#2650 G4）：AM 卡死时 `am force-stop
com.tinno.autotesttool` 挂满 30s 超时 → v2.3.7 把它记进 errors → 步骤失败 →
**整个 init 判败，当窗 monkey 不跑**。att_clean 的语义是「清残留防叠加」，
清不掉 ≠ 不能跑 monkey。

v2.3.8 锁定：
1. force-stop 超时/失败 → 降级 warning + `am kill` 兜底，步骤仍 success；
2. 超时收窄 30→10s（卡死设备上 30s×设备数拖垮整窗 init 预算）；
3. `am kill` 也失败 → 仍 success，warnings 里可见（继续 init）；
4. prefs 清理失败维持失败语义（#894 叠加风险依赖它，不随本次降级放宽）；
5. 对照：同场景 v2.3.7 判失败（变异对照锚点，钉住行为差异是本版本引入的）。
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[2] / "agent" / "scripts" / "monkey_setup"


def _load_version(version: str, tag: str):
    """按版本加载 _adb + monkey_setup；返回 (adb, mod)。

    脚本模块 `from _adb import adb_shell` 是导入期直绑——测试打桩必须落在
    `mod.adb_shell`（脚本模块自己的名字）上，patch _adb 模块无效。
    """
    d = _SCRIPTS / version
    adb = _load(f"_adb_{tag}", d / "_adb.py")
    sys.modules["_adb"] = adb
    mod = _load(f"monkey_setup_{tag}", d / "monkey_setup.py")
    return mod, mod


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _am_hang(adb_mod, calls: list):
    """AM 卡死形态：任何 `am ...` 命令都挂到超时。"""

    def fake_shell(command: str, timeout: int = 30) -> str:
        calls.append((command, timeout))
        if command.startswith("am "):
            raise subprocess.TimeoutExpired(cmd=command, timeout=timeout)
        return ""

    return fake_shell


def test_v238_force_stop_timeout_degrades_not_fails(monkeypatch):
    """force-stop 挂死：v2.3.8 必须 success + am kill 兜底 + 超时收窄到 10s。"""
    mod, _ = _load_version("v2.3.8", "v238")
    calls: list = []
    monkeypatch.setattr(mod, "adb_shell", _am_hang(mod, calls))

    result = mod.step_att_clean("serial-x", {})

    assert result["success"] is True, "清不掉 ≠ 不能跑 monkey，不得判败 init"
    assert result["att_prefs_cleared"] is True
    warnings = " ".join(result["att_warnings"])
    assert "force-stop" in warnings and "am kill" in warnings
    # 超时收窄：两次 am 尝试都是 10s（旧版 30s）
    am_calls = [c for c in calls if c[0].startswith("am ")]
    assert [t for _, t in am_calls] == [10, 10]
    # 兜底顺序：force-stop → am kill
    assert am_calls[0][0] == "am force-stop com.tinno.autotesttool"
    assert am_calls[1][0] == "am kill com.tinno.autotesttool"


def test_v238_both_channels_fail_still_continues(monkeypatch):
    """force-stop 与 am kill 双双挂死：仍 success，warnings 可见。"""
    mod, _ = _load_version("v2.3.8", "v238b")
    calls: list = []
    monkeypatch.setattr(mod, "adb_shell", _am_hang(mod, calls))

    result = mod.step_att_clean("serial-x", {})
    assert result["success"] is True
    assert any("均失败" in w for w in result["att_warnings"])


def test_v238_prefs_failure_still_fails(monkeypatch):
    """prefs 清理失败维持失败语义——本次降级只放宽 force-stop。"""
    mod, _ = _load_version("v2.3.8", "v238c")

    def fake_shell(command: str, timeout: int = 30) -> str:
        if command.startswith("am "):
            raise subprocess.TimeoutExpired(cmd=command, timeout=timeout)
        if "rm -rf" in command:
            raise subprocess.TimeoutExpired(cmd=command, timeout=timeout)
        return ""

    monkeypatch.setattr(mod, "adb_shell", fake_shell)
    result = mod.step_att_clean("serial-x", {})
    assert result["success"] is False
    assert "rm autotesttool prefs" in result["error"]


def test_v237_same_scenario_fails_contrast_anchor(monkeypatch):
    """对照锚点：同场景 v2.3.7 判失败——行为差异确由 v2.3.8 引入。"""
    mod, _ = _load_version("v2.3.7", "v237c")
    calls: list = []
    monkeypatch.setattr(mod, "adb_shell", _am_hang(mod, calls))

    result = mod.step_att_clean("serial-x", {})
    assert result["success"] is False
    assert "force-stop autotesttool" in result["error"]


def test_v238_happy_path_no_warnings(monkeypatch):
    """AM 正常：行为与旧版一致（success、无 warnings）。"""
    mod, _ = _load_version("v2.3.8", "v238d")

    def fake_shell(command: str, timeout: int = 30) -> str:
        return ""

    monkeypatch.setattr(mod, "adb_shell", fake_shell)
    result = mod.step_att_clean("serial-x", {})
    assert result == {"success": True, "att_prefs_cleared": True}
