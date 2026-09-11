"""#1492：pytest 进程内测试容器清理兜底（纯逻辑单测，不依赖 docker）。"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location(
    "container_lifecycle",
    REPO_ROOT / "backend" / "tests" / "container_lifecycle.py",
)
_mod = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
sys.modules["container_lifecycle"] = _mod
_spec.loader.exec_module(_mod)


class FakeContainer:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls = 0
        self.fail = fail

    def stop(self) -> None:
        self.calls += 1
        if self.fail:
            raise RuntimeError("stop failed")


class FakeAtexit:
    def __init__(self) -> None:
        self.registered: list = []

    def register(self, fn) -> None:
        self.registered.append(fn)


class FakeSignal:
    SIGTERM = 15
    SIGINT = 2
    SIG_DFL = 0

    def __init__(self) -> None:
        self.handlers: dict = {self.SIGTERM: self.SIG_DFL, self.SIGINT: self.SIG_DFL}
        self.set: list = []
        self.raised: list = []

    def getsignal(self, signum):
        return self.handlers.get(signum, self.SIG_DFL)

    def signal(self, signum, handler):
        self.handlers[signum] = handler
        self.set.append((signum, handler))
        return handler

    def raise_signal(self, signum):
        self.raised.append(signum)


class TestStop:
    def test_stop_is_idempotent(self):
        c = FakeContainer()
        g = _mod.ContainerCleanup(c)
        assert g.stop() is True
        assert g.stop() is False
        assert c.calls == 1
        assert g.stopped is True

    def test_stop_swallows_errors(self):
        c = FakeContainer(fail=True)
        g = _mod.ContainerCleanup(c)
        assert g.stop() is True  # 尝试过即返回 True，异常不外溢
        assert c.calls == 1

    def test_none_container_is_noop(self):
        g = _mod.ContainerCleanup(None)
        assert g.stop() is False
        fake_atexit, fake_signal = FakeAtexit(), FakeSignal()
        g.register(atexit_module=fake_atexit, signal_module=fake_signal)
        assert fake_atexit.registered == [] and fake_signal.set == []


class TestRegister:
    def test_registers_atexit_and_both_signals_once(self):
        g = _mod.ContainerCleanup(FakeContainer())
        fake_atexit, fake_signal = FakeAtexit(), FakeSignal()
        g.register(atexit_module=fake_atexit, signal_module=fake_signal)
        g.register(atexit_module=fake_atexit, signal_module=fake_signal)
        assert fake_atexit.registered == [g.stop]
        assert [s for s, _ in fake_signal.set] == [15, 2], "只挂一次"

    def test_sigterm_handler_stops_then_raises_default(self):
        c = FakeContainer()
        g = _mod.ContainerCleanup(c)
        fake_atexit, fake_signal = FakeAtexit(), FakeSignal()
        g.register(atexit_module=fake_atexit, signal_module=fake_signal)

        handler = fake_signal.handlers[15]
        handler(15, None)

        assert c.calls == 1, "收到 SIGTERM 必须先停容器"
        assert fake_signal.raised == [15], "SIG_DFL 时恢复默认并原样重发信号"
        assert fake_signal.handlers[15] is fake_signal.SIG_DFL

    def test_sigint_handler_chains_previous_callable(self):
        c = FakeContainer()
        g = _mod.ContainerCleanup(c)
        fake_atexit, fake_signal = FakeAtexit(), FakeSignal()
        chained: list = []
        fake_signal.handlers[2] = lambda signum, frame: chained.append(signum)

        g.register(atexit_module=fake_atexit, signal_module=fake_signal)
        handler = fake_signal.handlers[2]
        handler(2, None)

        assert c.calls == 1
        assert chained == [2], "可调用的前处理器必须链回（pytest 的 SIGINT 语义）"
        assert fake_signal.raised == [], "链回时不得再重发信号"
