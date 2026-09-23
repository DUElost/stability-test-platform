"""#3202 守卫：禁止「只把 `time.sleep` 变成 no-op、不推进时钟」的用例写法。

**为什么需要它**：2026-09-23 13:42 控制面宿主整机卡死（SwapFree 见底到 658 MiB、只能人工
按电源），元凶是 `backend/agent/tests/test_powercycle_scripts.py` 里两条用例——被测的
`install_apk` 用**真** `time.time()` 判 deadline（`wait_system_ready` 内 `while True` +
`time.sleep(min(5, remaining))`，默认 ready=60 s、budget=90 s），而用例只把 `sleep` 换成
no-op。后果不是"死循环"而是**把 60–90 秒墙钟跑满的忙等**，且每圈往 adb 桩的 list 里
`append` ⇒ 实测 ≈150 MB/s。空闲 CI runner 能扛（峰值 ~9 GB 后释放，所以 `pr-agent-tests`
是**绿的**），生产控制面宿主只剩 3–8 GiB 余量 ⇒ 冻结。**门禁绿而宿主死**正是这条守卫存在的理由。

判据粒度是**每个用例函数**，不是每个文件：该文件本来就有推进时钟的 `_patch_advancing_clock`
helper，按文件判会把真凶正好放过（写这条时实测过这个假阴性）。

按 #2639 的纪律，本文件同时**自证锚点在、判据有牙**（见 `test_guard_is_discriminative`）：
只扫得到一个函数级 no-op 形态、放过三种安全形态，且被扫对象确实在场。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
#: 本守卫的作用域：已冻结过宿主两次的这个文件（#3202）。
#: 扩到整个 backend/agent/tests 需要先逐个证明"被测循环不靠墙钟"，见 #3202 尾账。
TARGET = REPO_ROOT / "backend" / "agent" / "tests" / "test_powercycle_scripts.py"

# monkeypatch.setattr(<X>.time, "sleep", lambda ...: None)   ← 只有 no-op 形态危险
NOOP_SLEEP = re.compile(
    r"setattr\(\s*(?P<mod>[\w.]+)\.time\s*,\s*[\"']sleep[\"']\s*,\s*lambda[^:]*:\s*None\s*\)"
)
# 同一函数内必须有时钟推进：helper 或直接 stub time
ADVANCES_CLOCK = re.compile(
    r"_patch_advancing_clock\(|setattr\(\s*[\w.]+\.time\s*,\s*[\"']time[\"']"
)


def offending_functions(source: str) -> list[str]:
    """返回「把 sleep 变 no-op 却没推进时钟」的函数名（含类.函数限定名）。"""
    tree = ast.parse(source)
    found: list[str] = []

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                segment = ast.get_source_segment(source, child) or ""
                if NOOP_SLEEP.search(segment) and not ADVANCES_CLOCK.search(segment):
                    found.append(f"{prefix}{child.name}")
                walk(child, f"{prefix}{child.name}.")
            elif isinstance(child, ast.ClassDef):
                walk(child, f"{prefix}{child.name}.")
            else:
                walk(child, prefix)

    walk(tree, "")
    return sorted(found)


def test_anchor_is_present_before_judging_shape() -> None:
    """先证锚点在：被扫文件与它的安全夹具都还在，否则本守卫会在空转。"""
    assert TARGET.is_file(), f"{TARGET} 不在了——守卫的扫面需要跟着改，别让它静默失效"
    text = TARGET.read_text(encoding="utf-8")
    assert "_patch_advancing_clock" in text, "安全夹具被改名/删除：判据第二段已失效"
    assert '.time, "sleep"' in text or ".time, 'sleep'" in text, (
        "被扫对象里已不存在任何 sleep stub：请把作用域扩到 #3202 尾账里列的其余文件，"
        "不要留一条永远不会红的守卫"
    )


def test_no_noop_sleep_stub_without_advancing_clock() -> None:
    bad = offending_functions(TARGET.read_text(encoding="utf-8"))
    assert not bad, (
        "这些用例把 time.sleep 换成 no-op 却没有推进时钟——被测等待环靠真 time.time() 判 "
        "deadline 时，它会变成跑满 60–90 秒墙钟的忙等并按圈积累内存（#3202，实测 ≈150 MB/s，"
        "曾冻结控制面宿主）。改法：同函数内改用 _patch_advancing_clock(monkeypatch, mod)。\n"
        + "\n".join(f"  - {name}" for name in bad)
    )


def test_guard_is_discriminative(tmp_path: Path) -> None:
    """自证有牙：坏形态必须被抓住，三种安全形态必须放过。"""
    bad = '''
class TestOne:
    def test_spins(self, v103, monkeypatch):
        monkeypatch.setattr(v103.time, "sleep", lambda s: None)
        v103.install_apk(p)
'''
    good_helper = '''
class TestOne:
    def test_ok(self, v103, monkeypatch):
        _patch_advancing_clock(monkeypatch, v103)
        v103.install_apk(p)
'''
    good_stub_time = '''
class TestOne:
    def test_ok2(self, mod, monkeypatch):
        monkeypatch.setattr(mod.time, "sleep", lambda s: None)
        monkeypatch.setattr(mod.time, "time", lambda: 1000.0)
'''
    good_recording_body = '''
class TestOne:
    def test_ok3(self, mod, monkeypatch):
        sleeps = []
        monkeypatch.setattr(mod.time, "sleep", lambda s: sleeps.append(s))
'''
    assert offending_functions(bad) == ["TestOne.test_spins"], "坏形态没被抓住 ⇒ 判据退化"
    for name, src in (
        ("helper", good_helper),
        ("stub-time", good_stub_time),
        ("recording sleep body", good_recording_body),
    ):
        assert offending_functions(src) == [], f"安全形态被误判：{name}"
