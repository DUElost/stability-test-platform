"""#1998 P2 实时性：展锐 inotifyd 唤醒层（ADR-0032 D8 增补）。

三组覆盖：
1. UnisocUniviewReconciler 唤醒原语——wake() 截断 baseline 休眠、地板限频、
   stop 双路即时返回、wake_ticks 计数；
2. DeviceLogWatcher 路由——UNIVIEW 事件恒被消费为唤醒，不 emit、不进 puller；
3. JobSession 门控——opt-in 默认关；开启且平台=UNISOC 才注入 UNIVIEW 探测面。
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.agent.aee.unisoc_reconciler import UnisocUniviewReconciler
from backend.agent.device_platform import PLATFORM_MTK, PLATFORM_UNISOC
from backend.agent.watcher.batcher import DEFAULT_IMMEDIATE_CATEGORIES
from backend.agent.watcher.device_watcher import DeviceLogWatcher
from backend.agent.watcher.policy import WatcherPolicy
from backend.agent.watcher.sources import ProbeResult, WatcherCapability, WatcherEvent


# ----------------------------------------------------------------------
# 1) reconciler 唤醒原语
# ----------------------------------------------------------------------

class _NoopEmitter:
    def prepare(self, **_kw):
        raise AssertionError("唤醒路径不得产生信号")


def _make_wake_reconciler(tmp_path: Path, *, baseline: float) -> UnisocUniviewReconciler:
    return UnisocUniviewReconciler(
        signal_emitter=_NoopEmitter(),
        state_store=None,
        serial="UNI-WAKE",
        job_id=991,
        host_id="host-u",
        local_root=tmp_path / "aee_local",
        run_date_stamp="0919",
        baseline_interval_seconds=baseline,
        shell_fn=lambda *_a, **_k: None,
        pull_fn=lambda *_a, **_k: False,
        plan_run_id=7,
    )


def test_wait_baseline_without_wake(tmp_path, monkeypatch):
    """无唤醒且 baseline 已过 → 立即按 baseline 收口，不计 wake_ticks。"""
    r = _make_wake_reconciler(tmp_path, baseline=10.0)
    monkeypatch.setattr(
        r, "_last_tick_monotonic", time.monotonic() - 11.0,
    )
    assert r._wait_until_next_tick() == "baseline"
    assert r.stats.wake_ticks == 0


def test_wake_shortens_baseline_wait(tmp_path, monkeypatch):
    """baseline 很长 + 唤醒在场 → 下一拍被提前（秒级路径成立）。"""
    monkeypatch.setenv("STP_WATCHER_UNISOC_WAKE_MIN_INTERVAL_SECONDS", "0.05")
    r = _make_wake_reconciler(tmp_path, baseline=30.0)
    assert r._wake_min_interval == 0.05
    r._last_tick_monotonic = time.monotonic() - 1.0  # 地板已过
    r.wake()
    started = time.monotonic()
    assert r._wait_until_next_tick() == "wake"
    assert time.monotonic() - started < 1.0
    assert r.stats.wake_ticks == 0  # 计数发生在 _run，不在等待函数


def test_wake_respects_min_interval_floor(tmp_path, monkeypatch):
    """地板未到时，唤醒不允许把下一拍提前到地板之前。"""
    monkeypatch.setenv("STP_WATCHER_UNISOC_WAKE_MIN_INTERVAL_SECONDS", "1.0")
    r = _make_wake_reconciler(tmp_path, baseline=30.0)
    r._last_tick_monotonic = time.monotonic()  # 地板刚起：还差 ~1s
    r.wake()
    started = time.monotonic()
    assert r._wait_until_next_tick() == "wake"
    elapsed = time.monotonic() - started
    assert elapsed >= 0.9, f"地板未生效：唤醒后 {elapsed:.2f}s 即进入下一拍"
    assert elapsed <= 2.5


def test_stop_returns_promptly_and_counts_wake_ticks(tmp_path, monkeypatch):
    """stop() 置位唤醒事件 → 休眠两路即时返回；_run 循环内 wake_ticks 计数。"""
    monkeypatch.setenv("STP_WATCHER_UNISOC_WAKE_MIN_INTERVAL_SECONDS", "0.02")
    r = _make_wake_reconciler(tmp_path, baseline=0.5)
    ticks: list[int] = []
    monkeypatch.setattr(r, "tick_once", lambda: ticks.append(1))
    r.start()
    time.sleep(0.25)  # 首拍完成、进入 baseline 休眠
    r.wake()
    deadline = time.time() + 2.0
    while len(ticks) < 2 and time.time() < deadline:
        time.sleep(0.02)
    r.stop(timeout=2.0)
    assert len(ticks) >= 2, "唤醒后应提前进入第二拍"
    assert r.stats.wake_ticks >= 1


def test_stop_wakes_waiter_directly(tmp_path):
    """停机须即时打断 baseline 休眠（双路返回），而非等满一个周期。"""
    r = _make_wake_reconciler(tmp_path, baseline=30.0)
    r.start()
    time.sleep(0.2)
    started = time.monotonic()
    r.stop(timeout=2.0)
    assert time.monotonic() - started < 1.0, "stop 应即时返回，不等 baseline"


# ----------------------------------------------------------------------
# 2) DeviceLogWatcher 路由：UNIVIEW 只作唤醒
# ----------------------------------------------------------------------

@pytest.fixture
def db(tmp_path):
    from backend.agent.registry.local_db import LocalDB

    db = LocalDB()
    db.initialize(str(tmp_path / "agent.db"))
    yield db
    db.close()


def _watcher(db, **_kw) -> DeviceLogWatcher:
    probe = ProbeResult(
        capability=WatcherCapability.INOTIFYD_ROOT,
        accessible_categories=["UNIVIEW"],
        inaccessible_categories={},
        is_root=True,
        reasons={},
    )
    return DeviceLogWatcher(
        adb_path="adb", local_db=db,
        host_id="HOST", serial="UNI-W", job_id=992,
        policy=WatcherPolicy(batch_interval_seconds=10.0),
        capability=WatcherCapability.INOTIFYD_ROOT,
        probe_result=probe,
    )


def _event(category: str, filename: str = "JE.103000001") -> WatcherEvent:
    dir_path = "/data/ylog/uniview_exception"
    return WatcherEvent(
        category=category,
        event_mask="n",
        dir_path=dir_path,
        filename=filename,
        full_path=f"{dir_path}/{filename}",
        detected_at=datetime.now(timezone.utc),
    )


def test_uniview_event_wakes_and_never_emits(db, monkeypatch):
    watcher = _watcher(db)
    wakes: list[int] = []
    watcher.set_unisoc_wake(lambda: wakes.append(1))
    emits: list[WatcherEvent] = []
    monkeypatch.setattr(watcher, "_safe_emit", lambda ev, **kw: emits.append(ev))

    watcher._on_batch([_event("UNIVIEW"), _event("AEE")])
    assert len(wakes) == 1
    assert [e.category for e in emits] == ["AEE"], "UNIVIEW 不得经 inotifyd 路径 emit"


def test_uniview_event_does_not_reach_puller(db):
    """immediate 路径：UNIVIEW 不得触发 puller（拉取是 reconciler 独占）。"""
    watcher = _watcher(db)
    submits: list[WatcherEvent] = []
    watcher._puller = type(
        "_FakePuller", (), {"submit": staticmethod(lambda ev: submits.append(ev))},
    )()
    watcher._on_immediate(_event("UNIVIEW"))
    assert submits == []


class _BoomReconciler:
    def wake(self) -> None:
        raise RuntimeError("boom")


def test_wake_callback_exception_is_contained(db, monkeypatch):
    """回调异常只记日志，不得打断事件消费（也不得转成 emit）。"""
    watcher = _watcher(db)
    watcher.set_unisoc_wake(_BoomReconciler().wake)
    emits: list[WatcherEvent] = []
    monkeypatch.setattr(watcher, "_safe_emit", lambda ev, **kw: emits.append(ev))
    watcher._on_batch([_event("UNIVIEW")])
    assert emits == []


def test_uniview_without_callback_still_consumed(db, monkeypatch):
    """未接线（无 reconciler，如降级路径）时 UNIVIEW 同样不 emit——写入方唯一。"""
    watcher = _watcher(db)
    emits: list[WatcherEvent] = []
    monkeypatch.setattr(watcher, "_safe_emit", lambda ev, **kw: emits.append(ev))
    watcher._on_batch([_event("UNIVIEW")])
    assert emits == []


def test_uniview_is_immediate_category():
    """UNIVIEW 必须进 immediate 集：batch 默认 5s 会吃掉秒级收益（#1998）。"""
    assert "UNIVIEW" in DEFAULT_IMMEDIATE_CATEGORIES


# ----------------------------------------------------------------------
# 3) JobSession 门控：opt-in 默认关，仅 UNISOC 注入
# ----------------------------------------------------------------------

class _StubManager:
    def __init__(self, adb_path="adb"):
        self._adb_path = adb_path

    def get_dep(self, name):
        return self._adb_path if name == "adb_path" else None


def _bare_session(monkeypatch, *, gate: bool, platform: str):
    """绕过 __init__ 校验，仅铺门控所需字段（轻量单测）。"""
    from backend.agent.job_session import JobSession

    monkeypatch.setenv(
        "STP_WATCHER_UNISOC_INOTIFYD", "true" if gate else "false",
    )
    monkeypatch.setattr(
        "backend.agent.device_platform.detect_device_platform",
        lambda *_a, **_k: platform,
    )
    session = JobSession.__new__(JobSession)
    session._manager = _StubManager()
    session._policy = WatcherPolicy()
    session._serial = "UNI-G"
    session._job_id = 993
    return session


def test_gate_default_off_leaves_policy_untouched(monkeypatch):
    session = _bare_session(monkeypatch, gate=False, platform=PLATFORM_UNISOC)
    before = session._policy
    session._maybe_apply_unisoc_inotifyd_paths()
    assert session._policy is before
    assert "UNIVIEW" not in session._policy.paths


def test_gate_on_unisoc_injects_uniview_paths(monkeypatch):
    session = _bare_session(monkeypatch, gate=True, platform=PLATFORM_UNISOC)
    session._maybe_apply_unisoc_inotifyd_paths()
    assert list(session._policy.paths["UNIVIEW"]) == ["/data/ylog/uniview_exception"]
    assert session._policy.required_categories == ["UNIVIEW"]
    # MTK 缺省分类保留：探测/订阅面只增不改
    assert "AEE" in session._policy.paths


def test_gate_on_non_unisoc_leaves_policy_untouched(monkeypatch):
    session = _bare_session(monkeypatch, gate=True, platform=PLATFORM_MTK)
    session._maybe_apply_unisoc_inotifyd_paths()
    assert "UNIVIEW" not in session._policy.paths
    assert session._policy.required_categories != ["UNIVIEW"]
