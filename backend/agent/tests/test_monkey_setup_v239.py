"""monkey_setup v2.3.9：att_clean 按 rc 核验 + 结构化出口（#2862）。

缺陷（#2777 的后续）：v2.3.8 引入的降级语义没有可核验的判据——`adb_shell` 不看
returncode，只在超时时抛异常，于是

1. `am kill` rc≠0（设备 offline / 命令被拒）不抛异常 ⇒ 无条件记「已走兜底」；
2. `am force-stop` 非超时失败连 `except` 都不进 ⇒ 连 warning 都没有；
3. 两条都失败仍 `success=True, att_prefs_cleared=True`；prefs `rm` rc≠0 同理判绿。

v2.3.9 锁定：
1. 三条命令按 **rc** 定性（`adb_shell_quiet`），rc≠0 的 stderr 摘要进 warnings/errors；
2. 降级结论**结构化**：`metrics.att_stop_issued` / `metrics.att_prefs_cleared`
   （`pipeline_engine` 只读顶层 metrics，嵌套 `att_warnings` 进不了观测面）；
3. `att_prefs_cleared` 如实反映结果（失败路径不再返回 True）；
4. 语义不变的两条：force-stop/兜底失败仍**不判败** init（#2777 的意图）；prefs 清理
   失败仍判败（#894 的叠加风险依赖它）；
5. 对照锚点：同 rc 场景 v2.3.8 判绿且无 metrics——行为差异确由本版本引入。

边界（不主张）：`am kill` rc=0 不等于进程已死（它只杀「可安全杀」的后台进程），
本文件核验的是「命令是否被设备接受」。
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[2] / "agent" / "scripts" / "monkey_setup"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _load_version(version: str, tag: str):
    """按版本加载 _adb + monkey_setup；返回脚本模块。

    脚本模块 `from _adb import ...` 是导入期直绑——测试打桩必须落在
    `mod.adb_shell_quiet`（脚本模块自己的名字）上，patch `_adb` 模块无效。
    """
    d = _SCRIPTS / version
    adb = _load(f"_adb_{tag}", d / "_adb.py")
    sys.modules["_adb"] = adb
    return _load(f"monkey_setup_{tag}", d / "monkey_setup.py")


class _Proc:
    def __init__(self, returncode: int = 0, stderr: str = "") -> None:
        self.returncode = returncode
        self.stderr = stderr
        self.stdout = ""


def _rc_stub(plan: dict, calls: list):
    """按命令前缀回放 rc：`("am force-stop", 1)` / `("am kill", 0)` / `("rm -rf", 0)`。"""

    def fake_shell_quiet(command: str, timeout: int = 30):
        calls.append((command, timeout))
        for prefix, (rc, stderr) in plan.items():
            if prefix in command:
                return _Proc(rc, stderr)
        raise AssertionError(f"未预期的命令：{command}")

    return fake_shell_quiet


def test_all_ok_reports_structured_success(monkeypatch):
    mod = _load_version("v2.3.9", "v239a")
    calls: list = []
    monkeypatch.setattr(
        mod, "adb_shell_quiet",
        _rc_stub({"am force-stop": (0, ""), "rm -rf": (0, "")}, calls),
    )

    result = mod.step_att_clean("serial-x", {})

    assert result["success"] is True
    assert result["att_prefs_cleared"] is True
    assert result["metrics"] == {"att_stop_issued": 1, "att_prefs_cleared": 1}
    assert "att_warnings" not in result


def test_force_stop_rc_nonzero_falls_back_verified(monkeypatch):
    """force-stop 被拒（rc=1）→ am kill 兜底 rc=0：warning + 指标如实。"""
    mod = _load_version("v2.3.9", "v239b")
    calls: list = []
    monkeypatch.setattr(
        mod, "adb_shell_quiet",
        _rc_stub(
            {"am force-stop": (1, "Error: not found"), "am kill": (0, ""), "rm -rf": (0, "")},
            calls,
        ),
    )

    result = mod.step_att_clean("serial-x", {})

    assert result["success"] is True
    assert result["metrics"]["att_stop_issued"] == 1
    warnings = " ".join(result["att_warnings"])
    assert "rc=1" in warnings and "am kill 兜底" in warnings
    assert "Error: not found" in warnings          # stderr 摘要可见
    # 超时收窄与顺序（沿用 v2.3.8）
    am_calls = [c for c in calls if c[0].startswith("am ")]
    assert [t for _, t in am_calls] == [10, 10]
    assert am_calls[0][0] == "am force-stop com.tinno.autotesttool"
    assert am_calls[1][0] == "am kill com.tinno.autotesttool"


def test_both_stop_channels_nonzero_not_reported_as_issued(monkeypatch):
    """两条路的 rc 都非零：不得再把「未核验的兜底」记成成功（#2862 现象①）。"""
    mod = _load_version("v2.3.9", "v239c")
    calls: list = []
    monkeypatch.setattr(
        mod, "adb_shell_quiet",
        _rc_stub(
            {
                "am force-stop": (1, "device offline"),
                "am kill": (1, "device offline"),
                "rm -rf": (0, ""),
            },
            calls,
        ),
    )

    result = mod.step_att_clean("serial-x", {})

    assert result["success"] is True, "仍不判败 init（#2777 的意图不变）"
    assert result["metrics"]["att_stop_issued"] == 0, (
        "停止命令未被设备接受时必须如实记 0——否则「清理没做成」与「做成了」同形"
    )
    warnings = " ".join(result["att_warnings"])
    assert "am kill 兜底 rc=1" in warnings


def test_prefs_rc_nonzero_now_fails(monkeypatch):
    """prefs rm rc≠0（权限拒绝等）：判败——docstring 自陈「prefs 真失败值得红」。"""
    mod = _load_version("v2.3.9", "v239d")
    calls: list = []
    monkeypatch.setattr(
        mod, "adb_shell_quiet",
        _rc_stub(
            {"am force-stop": (0, ""), "rm -rf": (1, "Permission denied")},
            calls,
        ),
    )

    result = mod.step_att_clean("serial-x", {})

    assert result["success"] is False
    assert result["metrics"]["att_prefs_cleared"] == 0
    assert "rm autotesttool prefs rc=1" in result["error"]
    assert "Permission denied" in result["error"]


def test_timeout_still_degrades_and_verifies_fallback(monkeypatch):
    """超时路径（#2777 的原始形态）语义不变，但兜底改为**核验后**才记成功。"""
    mod = _load_version("v2.3.9", "v239e")
    calls: list = []

    def fake(command: str, timeout: int = 30):
        calls.append((command, timeout))
        if command.startswith("am force-stop"):
            raise subprocess.TimeoutExpired(cmd=command, timeout=timeout)
        return _Proc(0, "")

    monkeypatch.setattr(mod, "adb_shell_quiet", fake)
    result = mod.step_att_clean("serial-x", {})

    assert result["success"] is True
    assert result["metrics"] == {"att_stop_issued": 1, "att_prefs_cleared": 1}
    assert "已走 am kill 兜底" in " ".join(result["att_warnings"])


def test_v238_same_rc_scenario_reports_clean_contrast_anchor(monkeypatch):
    """对照锚点：同 rc 场景 v2.3.8 判绿且无 metrics（钉住差异由 v2.3.9 引入）。"""
    mod = _load_version("v2.3.8", "v238x")

    def fake_shell(command: str, timeout: int = 30) -> str:
        return ""          # v2.3.8 的 adb_shell 不看 rc：一切失败都退化成空 stdout

    monkeypatch.setattr(mod, "adb_shell", fake_shell)
    result = mod.step_att_clean("serial-x", {})

    assert result["success"] is True
    assert result["att_prefs_cleared"] is True       # ← rc 未知却报「已清」
    assert "metrics" not in result
