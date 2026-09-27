# -*- coding: utf-8 -*-
"""#3463 G1-sleep-2：sleep 三族 prefs **读路径**同样按 kind 分流（§9.2 ②③④⑦）。

上一轮（G1-sleep/#3466）把删除/整写判据收紧并让 `get_prefs_xml` root 优先，但
powercycle 侧复核 B2–B4 证明同一形态在读路径的另外三处仍在，规划者复查 sleep 三族
确认同样存在（#3463 §9.1 第 2 条），本文件钉住修复：

- ② `set_prefs(reset_count=false)`：读不到不得取 0 整写 full map（重启窗瞬时
  失败会覆盖续跑计数）——`ok` 解析、`absent` 取 0、其余 raise；
- ③ `start_task`：prefs 非 `ok` 时 raise，不得跳过 running=true 却照常启动服务；
- ④ `sleep_finish._verify_stop_flags`：回读走同源证据——root 写成功时不得因
  run-as 被拒而两轮读空假失败；真正读不到按既有语义重试后 raise；
- ⑦ `sleep_finish/_lib.py` 曾有两个同名 `_verify_stop_flags`（首个自递归、被第二个
  遮蔽＝死代码）——SourceGuard 钉「只出现一次」。

sleep 三族无 `resume_task`（那是 powercycle_check 巡检主链形态）；`sleep_check`
是只读巡检、无「收取窗口吞 raise」结构，按 §9.1 第一条不改其步骤语义。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from tools.dev.source_anchor import SourceGuard

REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPTS = REPO_ROOT / "backend" / "agent" / "scripts"
_FAMS = ("sleep_setup", "sleep_check", "sleep_finish")

_PREFS_PATH = "/data/data/com.tinno.autotesttool/shared_prefs/sleep_test_runner.xml"
_PROBE_PREFIX = "if [ ! -f "
_RUNAS_PREFIX = "run-as "
_HEALTHY_XML = (
    "<?xml version='1.0' encoding='utf-8' standalone='yes' ?>\n"
    "<map>\n"
    '    <int name="test_times" value="100"/>\n'
    '    <int name="current_count" value="37"/>\n'
    '    <boolean name="auto_resume" value="true"/>\n'
    '    <boolean name="running" value="true"/>\n'
    "</map>\n"
)

_CFG_RESUME = {
    "test_times": 100, "wake_seconds": 60, "sleep_seconds": 300,
    "tester": "tester", "auto_resume": True, "reset_count": False,
}

_loaded: dict[str, object] = {}


def _lib(fam: str):
    """按族加载 _lib.py（对齐 test_sleep_scripts.py 的 _load 姿势，进程内缓存一次）。"""
    if fam not in _loaded:
        path = _SCRIPTS / fam / "_lib.py"
        sys.path.insert(0, str(path.parent))
        try:
            sys.modules.pop("_lib", None)
            spec = importlib.util.spec_from_file_location(f"sleep_{fam}_lib_3463_g1sleep2", path)
            assert spec and spec.loader, f"cannot locate {path}"
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            _loaded[fam] = mod
        finally:
            sys.modules.pop("_lib", None)
            sys.path.remove(str(path.parent))
    return _loaded[fam]


def _install_fake_adb(monkeypatch, mod, routes: list[tuple[str, tuple]]):
    """按命令子串派发假 adb；返回全部调用记录。"""
    calls: list[list] = []

    def fake_adb(*args, timeout=60):
        calls.append([str(a) for a in args])
        cmd = str(args[1]) if len(args) > 1 else ""
        for needle, result in routes:
            if needle in cmd:
                return result
        return (0, "", "")

    monkeypatch.setattr(mod, "adb", fake_adb)
    return calls


def _pusher(monkeypatch, mod) -> list[str]:
    pushed: list[str] = []
    monkeypatch.setattr(mod, "push_prefs_xml", lambda content: pushed.append(content))
    return pushed


def _patch_advancing_clock(monkeypatch, mod, *, start: float = 1_000_000.0):
    """sleep 换掉真等待时必须推假时钟（#3202 守卫；同构 test_powercycle_scripts.py）。"""
    state = {"now": start}

    def fake_time() -> float:
        return state["now"]

    def fake_sleep(seconds: float) -> None:
        state["now"] += float(seconds)

    monkeypatch.setattr(mod.time, "time", fake_time)
    monkeypatch.setattr(mod.time, "sleep", fake_sleep)
    return state


def _cmds(calls: list[list]) -> str:
    return "\n".join(" ".join(c) for c in calls)


# ---------------------------------------------------------------------------
# ② set_prefs：reset_count=false 时按 kind 分流，读不到不得以 0 整写
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fam", _FAMS)
class TestSetPrefsKindBranch:
    def test_transient_raises_without_push(self, fam, monkeypatch):
        mod = _lib(fam)
        monkeypatch.setattr(mod, "is_root", lambda: True)
        _install_fake_adb(monkeypatch, mod, [(_PROBE_PREFIX, (-1, "", "timeout"))])
        pushed = _pusher(monkeypatch, mod)

        with pytest.raises(RuntimeError) as ei:
            mod.set_prefs(_CFG_RESUME)

        assert "可重试" in str(ei.value)
        assert pushed == [], f"瞬态读失败仍以 current_count=0 整写 full map：{pushed}"

    def test_denied_raises_without_push(self, fam, monkeypatch):
        mod = _lib(fam)
        monkeypatch.setattr(mod, "is_root", lambda: True)
        _install_fake_adb(monkeypatch, mod, [
            (_PROBE_PREFIX, (1, f"cat: {_PREFS_PATH}: Permission denied", "")),
        ])
        pushed = _pusher(monkeypatch, mod)

        with pytest.raises(RuntimeError) as ei:
            mod.set_prefs(_CFG_RESUME)

        assert "可重试" in str(ei.value)
        assert pushed == []

    def test_nonroot_runas_denied_raises_without_push(self, fam, monkeypatch):
        """run-as 恒拒（shared-uid non-debuggable）≠ 不存在：不得降 0 整写。"""
        mod = _lib(fam)
        monkeypatch.setattr(mod, "is_root", lambda: False)
        _install_fake_adb(monkeypatch, mod, [
            (_RUNAS_PREFIX, (1, "run-as: package not debuggable: com.tinno.autotesttool", "")),
        ])
        pushed = _pusher(monkeypatch, mod)

        with pytest.raises(RuntimeError):
            mod.set_prefs(_CFG_RESUME)

        assert pushed == []

    def test_absent_takes_zero_and_writes(self, fam, monkeypatch):
        """绿锚：absent 是合法整写输入（正常部署形态），current_count=0。"""
        mod = _lib(fam)
        monkeypatch.setattr(mod, "is_root", lambda: True)
        _install_fake_adb(monkeypatch, mod, [
            (_PROBE_PREFIX, (0, "__STP_PREFS_ABSENT__", "")),
        ])
        pushed = _pusher(monkeypatch, mod)

        assert mod.set_prefs(_CFG_RESUME) == 0
        assert len(pushed) == 1 and 'name="current_count" value="0"' in pushed[0]

    def test_ok_preserves_current_count(self, fam, monkeypatch):
        """绿锚：ok 解析续跑计数（原 37 不得丢）。"""
        mod = _lib(fam)
        monkeypatch.setattr(mod, "is_root", lambda: True)
        _install_fake_adb(monkeypatch, mod, [(_PROBE_PREFIX, (0, _HEALTHY_XML, ""))])
        pushed = _pusher(monkeypatch, mod)

        assert mod.set_prefs(_CFG_RESUME) == 37
        assert 'name="current_count" value="37"' in pushed[0]


# ---------------------------------------------------------------------------
# ③ start_task：prefs 非 ok 时 raise，不得跳过 running=true 却启动服务
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fam", _FAMS)
class TestStartTaskKindBranch:
    def test_transient_raises_before_service_start(self, fam, monkeypatch):
        mod = _lib(fam)
        monkeypatch.setattr(mod, "is_root", lambda: True)
        calls = _install_fake_adb(monkeypatch, mod, [(_PROBE_PREFIX, (-1, "", "timeout"))])
        pushed = _pusher(monkeypatch, mod)

        with pytest.raises(RuntimeError) as ei:
            mod.start_task()

        assert "可重试" in str(ei.value)
        assert "start-foreground-service" not in _cmds(calls), "prefs 未读成就启动了服务"
        assert "SLEEP_TEST_KEEPALIVE" not in _cmds(calls)
        assert pushed == []

    def test_nonroot_denied_raises_before_service_start(self, fam, monkeypatch):
        mod = _lib(fam)
        monkeypatch.setattr(mod, "is_root", lambda: False)
        calls = _install_fake_adb(monkeypatch, mod, [
            (_RUNAS_PREFIX, (1, "run-as: package not debuggable: com.tinno.autotesttool", "")),
        ])

        with pytest.raises(RuntimeError):
            mod.start_task()

        assert "start-foreground-service" not in _cmds(calls)

    def test_ok_writes_running_true_then_starts(self, fam, monkeypatch):
        """绿锚：ok 路径行为不变——置 running=true 后启动 Activity/服务/KEEPALIVE。"""
        mod = _lib(fam)
        monkeypatch.setattr(mod, "is_root", lambda: True)
        _patch_advancing_clock(monkeypatch, mod)
        calls = _install_fake_adb(monkeypatch, mod, [(_PROBE_PREFIX, (0, _HEALTHY_XML, ""))])
        pushed = _pusher(monkeypatch, mod)

        mod.start_task()

        assert len(pushed) == 1 and 'name="running" value="true"' in pushed[0]
        assert "current_count" in pushed[0]        # 原位更新，不丢既有字段
        assert "start-foreground-service" in _cmds(calls)


# ---------------------------------------------------------------------------
# ④⑦ sleep_finish：_verify_stop_flags 走同源证据 + 重复定义删除钉
# ---------------------------------------------------------------------------

class TestVerifyStopFlags:
    def test_root_write_ok_runas_denied_not_failed(self, monkeypatch):
        """复核 B4 主案：root 写已成功、回读时 run-as 恒拒——不得判失败。"""
        mod = _lib("sleep_finish")
        monkeypatch.setattr(mod, "is_root", lambda: True)
        written = _HEALTHY_XML.replace('value="true"', 'value="false"')
        calls = _install_fake_adb(monkeypatch, mod, [
            (_PROBE_PREFIX, (0, written, "")),
            (_RUNAS_PREFIX, (1, "run-as: package not debuggable: com.tinno.autotesttool", "")),
        ])

        mod._verify_stop_flags()   # 旧形态（get_prefs_xml 未 root 优先前）这里会 raise

        shell_cmds = [c for c in calls if len(c) > 1]
        assert shell_cmds and not any(_RUNAS_PREFIX in " ".join(c) for c in shell_cmds), \
            "root 可用时回读又走了 run-as（B4 假失败形态回归）"

    def test_persistently_unreadable_still_fails(self, monkeypatch):
        """真读不到（transient）：既有语义保留——重试 set_stop_flags 后仍不成立即 raise
        （重试路径由收紧后的 set_stop_flags 抛可重试失败）。"""
        mod = _lib("sleep_finish")
        monkeypatch.setattr(mod, "is_root", lambda: True)
        _install_fake_adb(monkeypatch, mod, [(_PROBE_PREFIX, (-1, "", "timeout"))])

        with pytest.raises(RuntimeError) as ei:
            mod._verify_stop_flags()

        assert "可重试" in str(ei.value)

    def test_verify_stop_flags_defined_exactly_once(self):
        """⑦ 源扫描钉：`def _verify_stop_flags` 在 sleep_finish/_lib.py 只出现一次——
        首个遮蔽版（含自递归）已删，不得复现（SourceGuard：锚点漂移与真实回归可区分，#2639）。"""
        guard = (
            SourceGuard.of_repo_path("backend/agent/scripts/sleep_finish/_lib.py")
            .anchored("def stop_task(")
        )
        guard.assert_count(
            "def _verify_stop_flags", 1,
            why="#3463 G1-sleep-2 ⑦：重复定义＝遮蔽死代码（首个含 _verify_stop_flags() 自递归），只允许生效定义一处",
        )

    def test_duplicate_definition_removed(self):
        """⑦：`def _verify_stop_flags` 在 sleep_finish/_lib.py 只允许出现一次
        （首个定义自递归且被遮蔽＝死代码；SourceGuard 锚在同文件的生效消费点）。"""
        guard = SourceGuard.of_repo_path(
            "backend/agent/scripts/sleep_finish/_lib.py"
        ).anchored("def stop_task(")
        guard.assert_count("def _verify_stop_flags", 1, why="#3463 G1-sleep-2 ⑦：不得再出现遮蔽式重复定义")
