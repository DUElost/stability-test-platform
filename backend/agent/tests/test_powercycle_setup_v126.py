# -*- coding: utf-8 -*-
"""#3223 账 1：powercycle_setup **v1.2.6**——等待环除时钟上界外必须有**轮次上界**。

2026-09-23 13:42 控制面宿主整机卡死的机制链里，最后一环就是这类循环：`time.sleep` 一旦
不消耗真实时间（假时钟夹具缺位、或将来任何同类改写），`while True` 的退出条件就只剩
`deadline - time.time()` ⇒ 循环在真墙钟走完前以每秒数十万次迭代跑满，且每次 adb 调用都往
调用方容器里 `append` ⇒ 实测 ≈150 MB/s（元凶进程 anon 120 s 内 2.6→15.3 GiB）。

⇒ **时钟上界在时钟不动时不是上界。** 本文件钉住轮次上界，并区分两种结束原因。

夹具口径：这里替换的是**模块属性 `mod.time`**（换成假对象），不去改 stdlib 的 `time`
——`monkeypatch.setattr(mod.time, "sleep", …)` 会改掉**整个进程**的时钟语义，那正是
#3202/#3223 的失控形态，测试自己不能成为下一个引信。
"""
from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPTS = REPO_ROOT / "backend" / "agent" / "scripts"


def _load(name: str, rel_path: str):
    path = _SCRIPTS / rel_path
    sys.path.insert(0, str(path.parent))
    try:
        sys.modules.pop("_lib", None)
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec and spec.loader, f"cannot locate {path}"
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.modules.pop("_lib", None)
        sys.path.remove(str(path.parent))


@pytest.fixture()
def lib_v126():
    return _load("powercycle_setup_lib_v126", "powercycle_setup/_lib.py")


class _FakeClock:
    """只读真实时间、但 `sleep` 什么都不做——即"睡而不等"的病态环境。"""

    def __init__(self) -> None:
        self.now = time.time
        self.sleep_calls = 0

    def time(self) -> float:
        return self.now()

    def sleep(self, seconds: float) -> None:
        self.sleep_calls += 1


def _stub_never_ready(mod, calls: list):
    def fake_ready():
        calls.append(1)
        return False, "boot_completed=''"

    mod.system_ready = fake_ready


def test_never_advancing_sleep_is_cut_off_by_poll_bound(lib_v126, monkeypatch):
    """病态路径：sleep 不推进时钟 + deadline 远在将来 ⇒ 必须由**轮次上界**截断。"""
    clock = _FakeClock()
    monkeypatch.setattr(lib_v126, "time", clock)
    seen: list = []
    _stub_never_ready(lib_v126, seen)

    started = time.time()
    ok, observed = lib_v126.wait_system_ready(clock.time() + 3600.0)   # 1h 后才到期
    elapsed = time.time() - started

    assert ok is False
    assert "polls_exhausted=400/400" in observed, observed
    assert len(seen) == 400, "轮次上界没起到约束作用"
    assert clock.sleep_calls == 399
    assert elapsed < 5.0, f"被截断后仍耗时 {elapsed:.1f}s"


def test_default_budget_never_hits_the_poll_bound(lib_v126, monkeypatch):
    """正控制：健康路径（sleep 真推进时钟）仍由 deadline 结束，轮次远不到 400。"""

    class _Advancing:
        def __init__(self) -> None:
            self.t = 1_000_000.0
            self.sleep_calls = 0

        def time(self) -> float:
            return self.t

        def sleep(self, seconds: float) -> None:
            self.sleep_calls += 1
            self.t += seconds

    clock = _Advancing()
    monkeypatch.setattr(lib_v126, "time", clock)
    seen: list = []
    _stub_never_ready(lib_v126, seen)

    ok, observed = lib_v126.wait_system_ready(clock.time() + 60.0)

    assert ok is False
    assert "deadline_exhausted" in observed, observed
    assert "polls_exhausted" not in observed
    # 60s 预算 / 5s 轮询 = 12 次 sleep，外加最后一次发现 remaining<=0 ⇒ 13 轮；
    # 断言写成"远小于 400 且等于算术值"，两头都锁：既不许误截断，也不许悄悄放宽轮询间隔。
    assert len(seen) == 13, f"轮数与 60s/5s 的算术不符：{len(seen)}"
    assert clock.sleep_calls == 12
    assert lib_v126.att_ready_max_polls() > len(seen) * 20, "冗余倍数被改小会让正常路径误截断"


def test_poll_bound_is_an_env_knob(lib_v126, monkeypatch):
    clock = _FakeClock()
    monkeypatch.setattr(lib_v126, "time", clock)
    monkeypatch.setenv("STP_ATT_READY_MAX_POLLS", "3")
    seen: list = []
    _stub_never_ready(lib_v126, seen)

    ok, observed = lib_v126.wait_system_ready(clock.time() + 3600.0)

    assert ok is False
    assert "polls_exhausted=3/3" in observed, observed
    assert len(seen) == 3


def test_ready_path_returns_immediately(lib_v126, monkeypatch):
    clock = _FakeClock()
    monkeypatch.setattr(lib_v126, "time", clock)
    lib_v126.system_ready = lambda: (True, "ready")

    assert lib_v126.wait_system_ready(clock.time() + 5.0) == (True, "ready")
    assert clock.sleep_calls == 0
