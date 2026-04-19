"""LogWatcherManager 单元测试（阶段 5A 真实现）。

覆盖：
    - configure 注入依赖（adb / adb_path / local_db / prober_factory / watcher_factory）
    - start 成功路径：probe OK → DeviceLogWatcher 创建 + start → watcher_state='active'
    - start 失败路径：
        * probe 抛 → 回滚登记 + 抛 WatcherStartError(code='probe_failed')
        * capability=UNAVAILABLE + on_unavailable=FAIL → 抛 probe_failed
        * capability=UNAVAILABLE + DEGRADED → 保留 handle，不创建 watcher，watcher_state='active'
        * capability=UNAVAILABLE + SKIP → 保留 handle，不写 watcher_state
        * watcher.start() 抛 → 回滚登记 + watcher_state='failed' + 抛
    - stop 成功路径：watcher.stop(drain, timeout) → handle.stats 回填 → watcher_state='stopped'
    - stop 幂等：已 stop 的 watcher_id 再 stop 返回 None
    - duplicate serial 保护：同 serial 二次 start 抛 already_running

策略：
    - 用 LocalDB in-memory SQLite（保证 watcher_state 真写盘）
    - 用 stub prober_factory / watcher_factory，避开真实 adb/inotifyd
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Optional
from unittest.mock import MagicMock

import pytest

from backend.agent.registry.local_db import LocalDB
from backend.agent.watcher.device_watcher import DeviceLogWatcher, WatcherStats
from backend.agent.watcher.exceptions import WatcherStartError
from backend.agent.watcher.manager import LogWatcherManager
from backend.agent.watcher.policy import OnUnavailableAction, WatcherPolicy
from backend.agent.watcher.sources import (
    CapabilityProber,
    ProbeResult,
    WatcherCapability,
)


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------

@pytest.fixture
def db(tmp_path):
    d = LocalDB()
    d.initialize(str(tmp_path / "agent.db"))
    yield d
    d.close()


@pytest.fixture(autouse=True)
def reset_manager():
    """每个用例前后重置单例。"""
    LogWatcherManager._reset_for_tests()
    yield
    LogWatcherManager._reset_for_tests()


# ----------------------------------------------------------------------
# Stub factories
# ----------------------------------------------------------------------

class _StubProber:
    """固定返回预置 ProbeResult；构造签名与 CapabilityProber 一致。"""

    def __init__(self, result_by_serial: dict):
        self._map = result_by_serial

    def probe(self, serial: str, policy: WatcherPolicy) -> ProbeResult:
        if serial not in self._map:
            raise RuntimeError(f"no_stub_for_serial_{serial}")
        return self._map[serial]


def _stub_prober_factory(result_by_serial: dict):
    """返回 manager.configure(prober_factory=...) 期待的工厂。"""
    def factory(adb, timeout):  # signature: (adb, timeout) -> prober
        return _StubProber(result_by_serial)
    return factory


@dataclass
class _StubWatcher:
    """DeviceLogWatcher 测试替身 —— 记录 start/stop 调用。"""

    adb_path: str = ""
    local_db: Any = None
    host_id: str = ""
    serial: str = ""
    job_id: int = 0
    policy: Any = None
    capability: Any = None
    probe_result: Any = None

    def __post_init__(self):
        self.start_called = False
        self.stop_called = False
        self.stop_args: dict = {}
        self._started = False
        self.stats_result = WatcherStats(
            events_total=5, events_dropped=0, signals_emitted=5,
            immediate_emits=2, batch_emits=1,
        )

    def start(self):
        self.start_called = True
        self._started = True

    def stop(self, *, drain: bool = True, timeout: float = 5.0) -> WatcherStats:
        self.stop_called = True
        self.stop_args = {"drain": drain, "timeout": timeout}
        return self.stats_result


class _FailingWatcher(_StubWatcher):
    def start(self):
        raise WatcherStartError("forced_start_failure", code="source_start_failed")


def _watcher_factory_producing(cls):
    """返回一个工厂：接收 DeviceLogWatcher 的 kwargs，透传给 cls(**kwargs)。"""
    produced: List[Any] = []

    def factory(**kwargs):
        inst = cls(**kwargs)
        produced.append(inst)
        return inst

    factory.produced = produced
    return factory


# ----------------------------------------------------------------------
# Probe fixtures
# ----------------------------------------------------------------------

def _probe_root() -> ProbeResult:
    return ProbeResult(
        capability=WatcherCapability.INOTIFYD_ROOT,
        accessible_categories=["ANR", "AEE"],
        inaccessible_categories={},
        is_root=True, reasons=[],
    )


def _probe_unavailable() -> ProbeResult:
    return ProbeResult(
        capability=WatcherCapability.UNAVAILABLE,
        accessible_categories=[],
        inaccessible_categories={"ANR": "requires_root", "AEE": "requires_root"},
        is_root=False, reasons=["adb_not_rooted", "ANR:requires_root"],
    )


# ----------------------------------------------------------------------
# configure / 守卫
# ----------------------------------------------------------------------

def test_start_without_configure_raises(db):
    mgr = LogWatcherManager.instance()
    with pytest.raises(WatcherStartError, match="not configured"):
        mgr.start(
            host_id="H", serial="S", job_id=1, log_dir="/tmp",
            policy=WatcherPolicy(),
        )


# ----------------------------------------------------------------------
# start 成功路径
# ----------------------------------------------------------------------

def test_start_success_creates_watcher_and_writes_active_state(db):
    mgr = LogWatcherManager.instance()
    wf = _watcher_factory_producing(_StubWatcher)
    mgr.configure(
        adb=MagicMock(), adb_path="adb", local_db=db,
        prober_factory=_stub_prober_factory({"S1": _probe_root()}),
        watcher_factory=wf,
    )

    handle = mgr.start(
        host_id="H1", serial="S1", job_id=101, log_dir="/tmp/j",
        policy=WatcherPolicy(),
    )

    # 登记表
    assert mgr.get_by_serial("S1") is handle
    assert mgr.get_by_id(handle.watcher_id) is handle
    # 真实 watcher 被创建并 start
    assert len(wf.produced) == 1
    assert wf.produced[0].start_called is True
    assert wf.produced[0].serial == "S1"
    assert wf.produced[0].job_id == 101
    # capability 已回填
    assert handle.capability == WatcherCapability.INOTIFYD_ROOT.value
    # watcher_state 写盘
    state = db.get_watcher_state(handle.watcher_id)
    assert state is not None
    assert state["state"] == "active"
    assert state["capability"] == "inotifyd_root"
    assert state["serial"] == "S1"
    assert state["job_id"] == 101


def test_duplicate_serial_raises_already_running(db):
    mgr = LogWatcherManager.instance()
    mgr.configure(
        adb=MagicMock(), adb_path="adb", local_db=db,
        prober_factory=_stub_prober_factory({"S1": _probe_root()}),
        watcher_factory=_watcher_factory_producing(_StubWatcher),
    )
    mgr.start(host_id="H", serial="S1", job_id=1, log_dir="/tmp", policy=WatcherPolicy())
    with pytest.raises(WatcherStartError) as excinfo:
        mgr.start(host_id="H", serial="S1", job_id=2, log_dir="/tmp", policy=WatcherPolicy())
    assert excinfo.value.code == "already_running"


# ----------------------------------------------------------------------
# start 失败路径
# ----------------------------------------------------------------------

def test_probe_exception_rolls_back_and_raises_probe_failed(db):
    """prober.probe() 抛 → 登记表回滚 + WatcherStartError(code=probe_failed)。"""
    mgr = LogWatcherManager.instance()

    def broken_factory(adb, timeout):
        class _BadProber:
            def probe(self, serial, policy):
                raise RuntimeError("adb dead")
        return _BadProber()

    mgr.configure(
        adb=MagicMock(), adb_path="adb", local_db=db,
        prober_factory=broken_factory,
        watcher_factory=_watcher_factory_producing(_StubWatcher),
    )
    with pytest.raises(WatcherStartError) as excinfo:
        mgr.start(host_id="H", serial="SB", job_id=1, log_dir="/tmp", policy=WatcherPolicy())
    assert excinfo.value.code == "probe_failed"
    # 登记表已回滚
    assert mgr.get_by_serial("SB") is None
    # watcher_state 未写（probe 失败早于首次写）
    assert db.list_active_watcher_states() == []


def test_unavailable_fail_policy_raises_probe_failed(db):
    mgr = LogWatcherManager.instance()
    mgr.configure(
        adb=MagicMock(), adb_path="adb", local_db=db,
        prober_factory=_stub_prober_factory({"SU": _probe_unavailable()}),
        watcher_factory=_watcher_factory_producing(_StubWatcher),
    )
    policy = WatcherPolicy(on_unavailable=OnUnavailableAction.FAIL)
    with pytest.raises(WatcherStartError) as excinfo:
        mgr.start(host_id="H", serial="SU", job_id=1, log_dir="/tmp", policy=policy)
    assert excinfo.value.code == "probe_failed"
    assert "reasons" in excinfo.value.context
    assert mgr.get_by_serial("SU") is None


def test_unavailable_degraded_keeps_handle_without_watcher(db):
    mgr = LogWatcherManager.instance()
    wf = _watcher_factory_producing(_StubWatcher)
    mgr.configure(
        adb=MagicMock(), adb_path="adb", local_db=db,
        prober_factory=_stub_prober_factory({"SD": _probe_unavailable()}),
        watcher_factory=wf,
    )
    policy = WatcherPolicy(on_unavailable=OnUnavailableAction.DEGRADED)
    handle = mgr.start(host_id="H", serial="SD", job_id=1, log_dir="/tmp", policy=policy)

    assert handle.impl is None, "DEGRADED 模式下不创建 DeviceLogWatcher"
    assert len(wf.produced) == 0
    assert handle.capability == "unavailable"
    assert mgr.get_by_serial("SD") is handle
    # watcher_state='active' 已写（便于运维可见 degraded）
    state = db.get_watcher_state(handle.watcher_id)
    assert state is not None
    assert state["state"] == "active"
    assert "degraded" in (state["last_error"] or "")


def test_unavailable_skip_keeps_handle_but_no_state(db):
    mgr = LogWatcherManager.instance()
    wf = _watcher_factory_producing(_StubWatcher)
    mgr.configure(
        adb=MagicMock(), adb_path="adb", local_db=db,
        prober_factory=_stub_prober_factory({"SK": _probe_unavailable()}),
        watcher_factory=wf,
    )
    policy = WatcherPolicy(on_unavailable=OnUnavailableAction.SKIP)
    handle = mgr.start(host_id="H", serial="SK", job_id=1, log_dir="/tmp", policy=policy)

    assert handle.impl is None
    assert len(wf.produced) == 0
    assert handle.capability == "skipped"
    # watcher_state 不写
    assert db.get_watcher_state(handle.watcher_id) is None


def test_watcher_start_failure_rolls_back_and_writes_failed_state(db):
    """watcher.start() 抛 WatcherStartError → 登记回滚 + state='failed' + 向上抛。"""
    mgr = LogWatcherManager.instance()
    mgr.configure(
        adb=MagicMock(), adb_path="adb", local_db=db,
        prober_factory=_stub_prober_factory({"SX": _probe_root()}),
        watcher_factory=_watcher_factory_producing(_FailingWatcher),
    )
    with pytest.raises(WatcherStartError) as excinfo:
        mgr.start(host_id="H", serial="SX", job_id=1, log_dir="/tmp", policy=WatcherPolicy())
    assert excinfo.value.code == "source_start_failed"
    # 登记表回滚
    assert mgr.get_by_serial("SX") is None
    # watcher_state='failed' 被写入
    with_failed = [
        s for s in _all_watcher_states(db) if s["state"] == "failed"
    ]
    assert len(with_failed) == 1
    assert with_failed[0]["serial"] == "SX"
    assert "start_failed" in (with_failed[0]["last_error"] or "")


# ----------------------------------------------------------------------
# stop 路径
# ----------------------------------------------------------------------

def test_stop_calls_real_watcher_stop_and_writes_stopped_state(db):
    mgr = LogWatcherManager.instance()
    wf = _watcher_factory_producing(_StubWatcher)
    mgr.configure(
        adb=MagicMock(), adb_path="adb", local_db=db,
        prober_factory=_stub_prober_factory({"ST": _probe_root()}),
        watcher_factory=wf,
    )
    handle = mgr.start(host_id="H", serial="ST", job_id=1, log_dir="/tmp", policy=WatcherPolicy())

    stopped = mgr.stop(handle.watcher_id, drain=True, timeout=2.0)
    assert stopped is handle
    # watcher.stop(drain=True, timeout=2.0) 被真调
    assert wf.produced[0].stop_called is True
    assert wf.produced[0].stop_args == {"drain": True, "timeout": 2.0}
    # handle.stats 回填（来自 _StubWatcher.stats_result）
    assert handle.stats["events_total"] == 5
    assert handle.stats["signals_emitted"] == 5
    # 登记表清空
    assert mgr.get_by_serial("ST") is None
    # watcher_state='stopped'
    state = db.get_watcher_state(handle.watcher_id)
    assert state["state"] == "stopped"
    assert state["stopped_at"] is not None


def test_stop_unknown_watcher_id_returns_none(db):
    mgr = LogWatcherManager.instance()
    mgr.configure(adb=MagicMock(), adb_path="adb", local_db=db)
    assert mgr.stop("wch-nonexistent", drain=False, timeout=0.5) is None


def test_stop_is_idempotent(db):
    mgr = LogWatcherManager.instance()
    mgr.configure(
        adb=MagicMock(), adb_path="adb", local_db=db,
        prober_factory=_stub_prober_factory({"SI": _probe_root()}),
        watcher_factory=_watcher_factory_producing(_StubWatcher),
    )
    handle = mgr.start(host_id="H", serial="SI", job_id=1, log_dir="/tmp", policy=WatcherPolicy())
    mgr.stop(handle.watcher_id, drain=False, timeout=0.5)
    # 第二次 stop 应返回 None 而非抛
    assert mgr.stop(handle.watcher_id, drain=False, timeout=0.5) is None


def test_stop_degraded_handle_without_impl(db):
    """DEGRADED 模式下 handle.impl=None；stop 仍更新 watcher_state='stopped'。"""
    mgr = LogWatcherManager.instance()
    mgr.configure(
        adb=MagicMock(), adb_path="adb", local_db=db,
        prober_factory=_stub_prober_factory({"SDD": _probe_unavailable()}),
        watcher_factory=_watcher_factory_producing(_StubWatcher),
    )
    handle = mgr.start(
        host_id="H", serial="SDD", job_id=1, log_dir="/tmp",
        policy=WatcherPolicy(on_unavailable=OnUnavailableAction.DEGRADED),
    )
    stopped = mgr.stop(handle.watcher_id)
    assert stopped is handle
    state = db.get_watcher_state(handle.watcher_id)
    assert state["state"] == "stopped"


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------

def _all_watcher_states(db: LocalDB):
    with db._lock:
        rows = db._conn.execute("SELECT * FROM watcher_state").fetchall()
    return [dict(r) for r in rows]
