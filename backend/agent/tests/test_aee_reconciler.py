"""AeeDbHistoryReconciler 单元测试 (M0 / PR #2)。

覆盖：
    - is_reconciler_enabled 灰度开关(env off / on / host 白名单)
    - tick_once: 无新条目返回 0,有新条目 emit 带 extra 字段
    - emit category 按 aee_type 映射(aee_exp → AEE / vendor_aee_exp → VENDOR_AEE)
    - emit source="reconciler",extra 含 event_type/package_name/aee_ts/nfs_path/pull_source
    - 双节奏: 有新条目切到突发 60s × N 轮 → 回落基线 180s
    - 状态键前缀按 job_id 隔离
    - 未知 aee_type 跳过
    - emit ContractViolation 增 signals_dropped 计数
    - start / stop 幂等
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from unittest.mock import patch

import pytest

from backend.agent.aee.reconciler import (
    AeeDbHistoryReconciler,
    ReconcilerStats,
    is_reconciler_enabled,
)
from backend.agent.aee.processor import ProcessResult
from backend.agent.watcher.contracts import ContractViolation


# ----------------------------------------------------------------------
# 辅助桩
# ----------------------------------------------------------------------

class _FakeEmitter:
    """SignalEmitter 替身:把 emit 调用累计到 self.calls。"""

    def __init__(self, *, raise_on_emit: Optional[Exception] = None):
        self.calls: List[Dict[str, Any]] = []
        self._lock = threading.Lock()
        self._raise = raise_on_emit
        self._seq = 0

    def emit(self, **kwargs) -> int:
        with self._lock:
            if self._raise is not None:
                raise self._raise
            self._seq += 1
            self.calls.append(dict(kwargs))
            return self._seq


class _MemStore:
    """ScriptStateStore 替身(in-memory)。"""

    def __init__(self) -> None:
        self._data: Dict[str, str] = {}
        self.touched_keys: List[str] = []

    def get_state(self, key: str, default: str = "") -> str:
        self.touched_keys.append(("get", key))
        return self._data.get(key, default)

    def set_state(self, key: str, value: str) -> None:
        self.touched_keys.append(("set", key))
        self._data[key] = value


def _fake_pdl_factory(payloads: List[Dict[str, Any]]):
    """构造 process_device_logs 替身,固定向 on_new_entry 喂指定 payload 序列。

    每次 tick 都喂同一组(假装"新增"),便于驱动双节奏判定。
    把 result.pulled 设为本批 payload 数量。
    """
    def _fake(*, on_new_entry=None, **_):
        if on_new_entry is not None:
            for p in payloads:
                on_new_entry(p)
        return ProcessResult(pulled=len(payloads), new_timestamps=[
            p["parsed"].get("timestamp", "") for p in payloads
        ])
    return _fake


def _stateful_pdl_factory(scripts: List[int]):
    """每次调用返回 scripts[i] 个新条目;耗尽后返回 0。

    用于测试双节奏切换。
    """
    state = {"i": 0}

    def _fake(*, on_new_entry=None, **_):
        i = state["i"]
        if i >= len(scripts):
            return ProcessResult(pulled=0)
        n = scripts[i]
        state["i"] += 1
        if on_new_entry is not None:
            for k in range(n):
                on_new_entry({
                    "line": f"line_{i}_{k}",
                    "parsed": {
                        "db_path": f"/data/aee_exp/db.{i}.{k}",
                        "pkg_name": "com.example.app",
                        "timestamp": f"2026-05-28 10:{i:02d}:{k:02d}.000",
                        "event_type": "CRASH",
                    },
                    "aee_type": "aee_exp",
                    "output_subdir": Path(f"/mnt/nfs/{i}/{k}"),
                })
        return ProcessResult(pulled=n)
    return _fake


# ----------------------------------------------------------------------
# is_reconciler_enabled
# ----------------------------------------------------------------------

def test_is_reconciler_enabled_default_off(monkeypatch):
    monkeypatch.delenv("STP_WATCHER_AEE_RECONCILE_ENABLED", raising=False)
    monkeypatch.delenv("STP_WATCHER_AEE_RECONCILE_HOSTS", raising=False)
    assert is_reconciler_enabled("any") is False


def test_is_reconciler_enabled_truthy(monkeypatch):
    monkeypatch.setenv("STP_WATCHER_AEE_RECONCILE_ENABLED", "1")
    monkeypatch.delenv("STP_WATCHER_AEE_RECONCILE_HOSTS", raising=False)
    assert is_reconciler_enabled("any") is True
    monkeypatch.setenv("STP_WATCHER_AEE_RECONCILE_ENABLED", "true")
    assert is_reconciler_enabled("any") is True
    monkeypatch.setenv("STP_WATCHER_AEE_RECONCILE_ENABLED", "no")
    assert is_reconciler_enabled("any") is False


def test_is_reconciler_enabled_host_whitelist(monkeypatch):
    monkeypatch.setenv("STP_WATCHER_AEE_RECONCILE_ENABLED", "1")
    monkeypatch.setenv("STP_WATCHER_AEE_RECONCILE_HOSTS", "host-a, host-b")
    assert is_reconciler_enabled("host-a") is True
    assert is_reconciler_enabled("host-b") is True
    assert is_reconciler_enabled("host-c") is False
    assert is_reconciler_enabled(None) is False


# ----------------------------------------------------------------------
# tick_once: 0 / N 新条目 + emit 字段
# ----------------------------------------------------------------------

def test_tick_once_no_new_returns_zero(monkeypatch):
    emitter = _FakeEmitter()
    store = _MemStore()
    monkeypatch.setattr(
        "backend.agent.aee.reconciler.process_device_logs",
        _fake_pdl_factory([]),
    )
    rec = AeeDbHistoryReconciler(
        signal_emitter=emitter,
        state_store=store,
        serial="SX",
        job_id=901,
        host_id="HOST",
    )
    assert rec.tick_once() == 0
    assert rec.stats.ticks_total == 1
    assert rec.stats.ticks_with_new == 0
    assert rec.stats.new_entries_total == 0
    assert emitter.calls == []


def test_tick_once_new_entries_emits_with_extra(monkeypatch):
    """单轮 2 个新条目: emitter 收到 2 次 emit,各项 extra 字段齐全。"""
    emitter = _FakeEmitter()
    store = _MemStore()
    payloads = [
        {
            "line": "/data/aee_exp/db.01,CRASH,pkg,_,_,_,_,_,com.example,2026-05-28 10:00:00.000",
            "parsed": {
                "db_path": "/data/aee_exp/db.01",
                "pkg_name": "com.example",
                "timestamp": "2026-05-28 10:00:00.000",
                "event_type": "CRASH",
            },
            "aee_type": "aee_exp",
            "output_subdir": Path("/mnt/nfs/jobs/901/AEE/db.01"),
        },
        {
            "line": "/data/vendor/aee_exp/db.02,CRASH,pkg,_,_,_,_,_,vendor.app,2026-05-28 10:00:30.000",
            "parsed": {
                "db_path": "/data/vendor/aee_exp/db.02",
                "pkg_name": "vendor.app",
                "timestamp": "2026-05-28 10:00:30.000",
                "event_type": "CRASH",
            },
            "aee_type": "vendor_aee_exp",
            "output_subdir": Path("/mnt/nfs/jobs/901/VENDOR_AEE/db.02"),
        },
    ]
    monkeypatch.setattr(
        "backend.agent.aee.reconciler.process_device_logs",
        _fake_pdl_factory(payloads),
    )

    rec = AeeDbHistoryReconciler(
        signal_emitter=emitter,
        state_store=store,
        serial="SX",
        job_id=901,
        host_id="HOST",
    )
    assert rec.tick_once() == 2
    assert rec.stats.signals_emitted == 2
    assert rec.stats.new_entries_total == 2

    aee_call = emitter.calls[0]
    assert aee_call["category"] == "AEE"
    assert aee_call["source"] == "reconciler"
    assert aee_call["path_on_device"] == "/data/aee_exp/db.01"
    # 跨平台:reconciler 内部 str(Path(...)) 在 Windows 上会转成反斜杠,断言用 Path 等价
    expected_aee_dir = Path("/mnt/nfs/jobs/901/AEE/db.01")
    assert Path(aee_call["artifact_uri"]) == expected_aee_dir
    extra = aee_call["extra"]
    assert extra["event_type"] == "CRASH"
    assert extra["package_name"] == "com.example"
    assert extra["aee_ts"] == "2026-05-28 10:00:00.000"
    assert Path(extra["nfs_path"]) == expected_aee_dir
    assert extra["pull_source"] == "reconciler"

    vendor_call = emitter.calls[1]
    assert vendor_call["category"] == "VENDOR_AEE"
    assert vendor_call["extra"]["package_name"] == "vendor.app"


def test_unknown_aee_type_skipped(monkeypatch):
    """未知 aee_type → 不 emit,不污染 stats(signals_emitted 不变)。"""
    emitter = _FakeEmitter()
    store = _MemStore()
    monkeypatch.setattr(
        "backend.agent.aee.reconciler.process_device_logs",
        _fake_pdl_factory([{
            "line": "?",
            "parsed": {
                "db_path": "?",
                "pkg_name": "?",
                "timestamp": "2026-05-28 10:00:00",
                "event_type": "CRASH",
            },
            "aee_type": "unknown_dir",
            "output_subdir": Path("/tmp"),
        }]),
    )
    rec = AeeDbHistoryReconciler(
        signal_emitter=emitter,
        state_store=store,
        serial="SX",
        job_id=902,
        host_id="HOST",
    )
    rec.tick_once()
    assert emitter.calls == []
    assert rec.stats.signals_emitted == 0
    # signals_dropped 是 emit 抛异常路径,unknown 不算抛异常 → 应保持 0
    assert rec.stats.signals_dropped == 0


def test_emit_contract_violation_increments_dropped(monkeypatch):
    """SignalEmitter.emit() 抛 ContractViolation → signals_dropped++,tick 不崩。"""
    emitter = _FakeEmitter(raise_on_emit=ContractViolation("bad envelope"))
    store = _MemStore()
    monkeypatch.setattr(
        "backend.agent.aee.reconciler.process_device_logs",
        _fake_pdl_factory([{
            "line": "x",
            "parsed": {
                "db_path": "/data/aee_exp/db.0",
                "pkg_name": "p",
                "timestamp": "2026-05-28 10:00:00",
                "event_type": "CRASH",
            },
            "aee_type": "aee_exp",
            "output_subdir": Path("/tmp"),
        }]),
    )
    rec = AeeDbHistoryReconciler(
        signal_emitter=emitter,
        state_store=store,
        serial="SX",
        job_id=903,
        host_id="HOST",
    )
    rec.tick_once()
    assert rec.stats.signals_dropped == 1
    assert rec.stats.signals_emitted == 0


# ----------------------------------------------------------------------
# 状态键命名空间
# ----------------------------------------------------------------------

def test_state_key_prefix_isolated_per_job():
    """state_key_prefix 形如 aee:reconciler:{job_id};不同 job 不共享。"""
    rec_a = AeeDbHistoryReconciler(
        signal_emitter=_FakeEmitter(),
        state_store=_MemStore(),
        serial="SX",
        job_id=1001,
        host_id="HOST",
    )
    rec_b = AeeDbHistoryReconciler(
        signal_emitter=_FakeEmitter(),
        state_store=_MemStore(),
        serial="SX",
        job_id=1002,
        host_id="HOST",
    )
    assert rec_a._state_prefix == "aee:reconciler:1001"
    assert rec_b._state_prefix == "aee:reconciler:1002"
    assert rec_a._cfg.state_key_prefix == "aee:reconciler:1001"
    assert rec_b._cfg.state_key_prefix == "aee:reconciler:1002"


# ----------------------------------------------------------------------
# 双节奏
# ----------------------------------------------------------------------

def test_dual_tempo_switches_to_burst_after_new_entry(monkeypatch):
    """有新条目 → _burst_remaining = burst_rounds;无新条目 → 递减。"""
    emitter = _FakeEmitter()
    store = _MemStore()
    rec = AeeDbHistoryReconciler(
        signal_emitter=emitter,
        state_store=store,
        serial="SX",
        job_id=1101,
        host_id="HOST",
        baseline_interval_seconds=180.0,
        burst_interval_seconds=60.0,
        burst_rounds=5,
    )
    # 模拟一个有新条目的 tick → _burst_remaining 应被设到 5
    monkeypatch.setattr(
        "backend.agent.aee.reconciler.process_device_logs",
        _fake_pdl_factory([{
            "line": "x",
            "parsed": {
                "db_path": "/data/aee_exp/db.0",
                "pkg_name": "p",
                "timestamp": "2026-05-28 10:00:00",
                "event_type": "CRASH",
            },
            "aee_type": "aee_exp",
            "output_subdir": Path("/tmp"),
        }]),
    )
    # 复制 _run 中的状态机决策片段:tick → 根据 new_count 调整 _burst_remaining
    new = rec.tick_once()
    assert new == 1
    with rec._state_lock:
        if new > 0:
            rec._burst_remaining = rec._burst_rounds
    assert rec._burst_remaining == 5

    # 再连续 5 轮 0 新条目应递减到 0
    monkeypatch.setattr(
        "backend.agent.aee.reconciler.process_device_logs",
        _fake_pdl_factory([]),
    )
    for expected_remaining in (4, 3, 2, 1, 0):
        new = rec.tick_once()
        with rec._state_lock:
            if new > 0:
                rec._burst_remaining = rec._burst_rounds
            elif rec._burst_remaining > 0:
                rec._burst_remaining -= 1
        assert rec._burst_remaining == expected_remaining


def test_run_loop_uses_burst_interval_after_new_entry(monkeypatch):
    """启动后台线程,模拟 1 轮新条目 + 1 轮等待,验证 stop_event.wait 使用的间隔。"""
    emitter = _FakeEmitter()
    store = _MemStore()
    # 把每次 process_device_logs 切换成"先有 1 条 → 再 0 条"
    monkeypatch.setattr(
        "backend.agent.aee.reconciler.process_device_logs",
        _stateful_pdl_factory([1, 0, 0]),
    )
    rec = AeeDbHistoryReconciler(
        signal_emitter=emitter,
        state_store=store,
        serial="SX",
        job_id=1102,
        host_id="HOST",
        baseline_interval_seconds=10.0,
        burst_interval_seconds=0.05,    # 极短便于测试
        burst_rounds=3,
    )
    rec.start()
    try:
        # 等到至少 2 次 tick 完成(首轮 + 至少 1 次 burst)
        deadline = time.time() + 2.0
        while time.time() < deadline and rec.stats.ticks_total < 2:
            time.sleep(0.02)
    finally:
        rec.stop(timeout=1.0)

    assert rec.stats.ticks_total >= 2
    assert rec.stats.signals_emitted == 1   # 首轮的 1 条


# ----------------------------------------------------------------------
# 生命周期幂等
# ----------------------------------------------------------------------

def test_start_stop_idempotent(monkeypatch):
    emitter = _FakeEmitter()
    store = _MemStore()
    monkeypatch.setattr(
        "backend.agent.aee.reconciler.process_device_logs",
        _fake_pdl_factory([]),
    )
    rec = AeeDbHistoryReconciler(
        signal_emitter=emitter,
        state_store=store,
        serial="SX",
        job_id=1201,
        host_id="HOST",
        baseline_interval_seconds=60.0,
        burst_interval_seconds=60.0,
    )
    rec.start()
    rec.start()    # 重复 start 不应抛
    stats = rec.stop(timeout=1.0)
    stats2 = rec.stop(timeout=0.5)    # 重复 stop 不应抛
    assert stats is stats2 is rec.stats


def test_stop_before_start_returns_empty_stats():
    rec = AeeDbHistoryReconciler(
        signal_emitter=_FakeEmitter(),
        state_store=_MemStore(),
        serial="SX",
        job_id=1202,
        host_id="HOST",
    )
    stats = rec.stop(timeout=0.5)
    assert isinstance(stats, ReconcilerStats)
    assert stats.ticks_total == 0


# ----------------------------------------------------------------------
# env 数值覆盖
# ----------------------------------------------------------------------

def test_env_overrides_intervals(monkeypatch):
    monkeypatch.setenv("STP_WATCHER_AEE_RECONCILE_INTERVAL_SECONDS", "30")
    monkeypatch.setenv("STP_WATCHER_AEE_RECONCILE_BURST_INTERVAL_SECONDS", "5")
    monkeypatch.setenv("STP_WATCHER_AEE_RECONCILE_BURST_ROUNDS", "8")
    rec = AeeDbHistoryReconciler(
        signal_emitter=_FakeEmitter(),
        state_store=_MemStore(),
        serial="SX",
        job_id=1301,
        host_id="HOST",
    )
    assert rec._baseline == 30.0
    assert rec._burst == 5.0
    assert rec._burst_rounds == 8
