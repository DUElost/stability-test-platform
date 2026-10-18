"""#3730 U5 v1.7.1：等待上线 15s PROGRESS 轮询（§3.6.2）。"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
SCRIPTS = REPO / "backend" / "agent" / "scripts"


def _load_disarm():
    path = SCRIPTS / "powercycle_disarm" / "powercycle_disarm.py"
    lib_path = path.parent / "_lib.py"
    lib_spec = importlib.util.spec_from_file_location("pc_disarm_wait_lib", lib_path)
    assert lib_spec and lib_spec.loader
    lib = importlib.util.module_from_spec(lib_spec)
    sys.modules["pc_disarm_wait_lib"] = lib
    lib_spec.loader.exec_module(lib)
    sys.modules["_lib"] = lib
    spec = importlib.util.spec_from_file_location("pc_disarm_wait", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["pc_disarm_wait"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def disarm_mod():
    return _load_disarm()


class TestWaitDeviceOnlineProgress:
    def test_stamps_after_each_poll_within_15s_and_respects_budget(self, disarm_mod):
        stamps = []
        clock = {"t": 0.0}

        def now():
            return clock["t"]

        def sleep(dt):
            clock["t"] += float(dt)

        polls = {"n": 0}

        def online(timeout):
            polls["n"] += 1
            assert timeout <= 15
            # 快速失败仍消耗极短时间；假时钟必须推进，否则 sleep(0) 会死循环
            clock["t"] += 0.05
            return clock["t"] >= 75.0

        def boot(timeout):
            assert timeout <= 15
            clock["t"] += 0.01
            return "1" if clock["t"] >= 75.0 else "0"

        ok = disarm_mod._wait_device_online(
            600,
            clock=now,
            sleeper=sleep,
            online_fn=online,
            boot_prop_fn=boot,
            progress_fn=lambda p: stamps.append((clock["t"], dict(p))),
        )
        assert ok is True
        assert stamps, "必须产生 PROGRESS"
        times = [t for t, _ in stamps]
        for a, b in zip(times, times[1:], strict=False):
            assert b - a <= 15.0 + 1e-6, (a, b, b - a)
        seqs = [p["seq"] for _, p in stamps]
        assert seqs == list(range(1, len(seqs) + 1))
        assert polls["n"] >= 2
        assert times[-1] >= 75.0

    def test_budget_expiry_stops(self, disarm_mod):
        stamps = []
        clock = {"t": 0.0}

        def now():
            return clock["t"]

        def sleep(dt):
            clock["t"] += float(dt)

        def online(timeout):
            # 每次轮询推进，模拟真实 get-state 耗时，使截止可到达
            clock["t"] += min(1.0, float(timeout))
            return False

        ok = disarm_mod._wait_device_online(
            30,
            clock=now,
            sleeper=sleep,
            online_fn=online,
            boot_prop_fn=lambda t: "0",
            progress_fn=lambda p: stamps.append(dict(p)),
        )
        assert ok is False
        assert stamps
        assert clock["t"] >= 30.0
        assert len(stamps) <= 40

    def test_slow_get_state_14s_keeps_adjacent_stamps_le_15(self, disarm_mod):
        """替身 get-state 耗时 14s 时，相邻 PROGRESS 仍 ≤15s（§3.6.2 v1.7.1）。"""
        stamps = []
        clock = {"t": 0.0}

        def now():
            return clock["t"]

        def sleep(dt):
            clock["t"] += float(dt)

        polls = {"n": 0}

        def online(timeout):
            polls["n"] += 1
            assert 1 <= timeout <= 15
            clock["t"] += min(14.0, float(timeout))
            return polls["n"] >= 3

        def boot(timeout):
            assert 1 <= timeout <= 15
            clock["t"] += min(0.5, float(timeout))
            return "1" if polls["n"] >= 3 else "0"

        ok = disarm_mod._wait_device_online(
            600,
            clock=now,
            sleeper=sleep,
            online_fn=online,
            boot_prop_fn=boot,
            progress_fn=lambda p: stamps.append((clock["t"], dict(p))),
        )
        assert ok is True
        times = [t for t, _ in stamps]
        assert len(times) >= 2
        for a, b in zip(times, times[1:], strict=False):
            assert b - a <= 15.0 + 1e-6, (a, b, b - a, times)

    def test_get_state_hits_full_timeout_keeps_adjacent_stamps_le_15(self, disarm_mod):
        """替身命中本轮 get-state 超时上限时，相邻戳仍 ≤15s。"""
        stamps = []
        clock = {"t": 0.0}

        def now():
            return clock["t"]

        def sleep(dt):
            clock["t"] += float(dt)

        polls = {"n": 0}

        def online(timeout):
            polls["n"] += 1
            assert 1 <= timeout <= 15
            clock["t"] += float(timeout)
            return polls["n"] >= 2

        def boot(timeout):
            clock["t"] += min(0.2, float(timeout))
            return "1" if polls["n"] >= 2 else "0"

        ok = disarm_mod._wait_device_online(
            600,
            clock=now,
            sleeper=sleep,
            online_fn=online,
            boot_prop_fn=boot,
            progress_fn=lambda p: stamps.append((clock["t"], dict(p))),
        )
        assert ok is True
        times = [t for t, _ in stamps]
        assert len(times) >= 2
        for a, b in zip(times, times[1:], strict=False):
            assert b - a <= 15.0 + 1e-6, (a, b, b - a, times)
