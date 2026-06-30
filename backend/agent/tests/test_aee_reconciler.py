"""AeeDbHistoryReconciler 单元测试 (M0 / PR #2)。

覆盖：
    - is_reconciler_enabled 灰度开关(env off / on / host 白名单)
    - tick_once: 无新条目返回 0,有新条目 emit 带 extra 字段
    - emit category 按 aee_type 映射(aee_exp → AEE / vendor_aee_exp → VENDOR_AEE)
    - emit source="reconciler",extra 含 event_type/package_name/aee_ts/nfs_path/pull_source
    - 双节奏: 有新条目切到突发 60s × N 轮 → 回落基线 180s
    - M3 watcher:aee 状态键命名空间与 legacy scan_aee 迁移
    - 未知 aee_type 跳过
    - emit ContractViolation 增 signals_dropped 计数
    - start / stop 幂等
"""

from __future__ import annotations

import json
import os
import subprocess
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

def test_is_reconciler_enabled_default_on(monkeypatch):
    """ADR-0018 2026-06-18: 默认开（default=True）。"""
    monkeypatch.delenv("STP_WATCHER_AEE_RECONCILE_ENABLED", raising=False)
    monkeypatch.delenv("STP_WATCHER_AEE_RECONCILE_HOSTS", raising=False)
    assert is_reconciler_enabled("any") is True


def test_is_reconciler_enabled_truthy(monkeypatch):
    monkeypatch.setenv("STP_WATCHER_AEE_RECONCILE_ENABLED", "1")
    monkeypatch.delenv("STP_WATCHER_AEE_RECONCILE_HOSTS", raising=False)
    assert is_reconciler_enabled("any") is True
    monkeypatch.setenv("STP_WATCHER_AEE_RECONCILE_ENABLED", "true")
    assert is_reconciler_enabled("any") is True
    monkeypatch.setenv("STP_WATCHER_AEE_RECONCILE_ENABLED", "no")
    assert is_reconciler_enabled("any") is False


def test_is_reconciler_enabled_explicit_false(monkeypatch):
    """显式关闭。"""
    monkeypatch.setenv("STP_WATCHER_AEE_RECONCILE_ENABLED", "false")
    monkeypatch.delenv("STP_WATCHER_AEE_RECONCILE_HOSTS", raising=False)
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
        baseline_snapshot_enabled=False,
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
            "line": "/data/aee_exp/db.01,Java (JE),pkg,_,_,_,_,_,com.example,2026-05-28 10:00:00.000",
            "parsed": {
                "db_path": "/data/aee_exp/db.01",
                "pkg_name": "com.example",
                "timestamp": "2026-05-28 10:00:00.000",
                "event_type": "CRASH",
                "raw_event_type": "Java (JE)",
                "event_subtype": "JE",
            },
            "aee_type": "aee_exp",
            "output_subdir": Path("/mnt/nfs/jobs/901/AEE/db.01"),
        },
        {
            "line": "/data/vendor/aee_exp/db.02,System API Dump,pkg,_,_,_,_,_,vendor.app,2026-05-28 10:00:30.000",
            "parsed": {
                "db_path": "/data/vendor/aee_exp/db.02",
                "pkg_name": "vendor.app",
                "timestamp": "2026-05-28 10:00:30.000",
                "event_type": "CRASH",
                "raw_event_type": "System API Dump",
                "event_subtype": "System API Dump",
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
        baseline_snapshot_enabled=False,
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
    assert extra["raw_event_type"] == "Java (JE)"
    assert extra["event_subtype"] == "JE"
    assert extra["package_name"] == "com.example"
    assert extra["aee_ts"] == "2026-05-28 10:00:00.000"
    assert Path(extra["nfs_path"]) == expected_aee_dir
    assert extra["pull_source"] == "reconciler"
    assert extra["entry_origin"] == "runtime"
    assert extra["schema_version"] == 2

    vendor_call = emitter.calls[1]
    assert vendor_call["category"] == "VENDOR_AEE"
    assert vendor_call["extra"]["package_name"] == "vendor.app"
    assert vendor_call["extra"]["raw_event_type"] == "System API Dump"
    assert vendor_call["extra"]["event_subtype"] == "System API Dump"
    assert vendor_call["extra"]["entry_origin"] == "runtime"
    assert vendor_call["extra"]["schema_version"] == 2


def test_first_tick_runs_baseline_snapshot_and_marks_runtime_processed(monkeypatch):
    """首轮先导出设备存量问题,并并入共享 processed key 防止同 Job 重复。"""
    emitter = _FakeEmitter()
    store = _MemStore()
    calls: list[str] = []
    payload = {
        "line": "/data/aee_exp/db.20,Native (NE),pkg,_,_,_,_,_,com.settings,2026-05-28 10:00:00.000",
        "parsed": {
            "db_path": "/data/aee_exp/db.20",
            "pkg_name": "com.settings",
            "timestamp": "2026-05-28 10:00:00.000",
            "event_type": "CRASH",
            "raw_event_type": "Native (NE)",
            "event_subtype": "NE",
        },
        "aee_type": "aee_exp",
        "output_subdir": Path("/mnt/nfs/jobs/904/AEE/db.20"),
    }

    def fake_pdl(*, config, on_new_entry=None, **_):
        calls.append(
            f"{config.state_key_prefix}|ml={config.export_mobilelog}|br={config.export_bugreport}"
        )
        if config.state_key_prefix == "watcher_baseline:904":
            if on_new_entry is not None:
                on_new_entry(dict(payload))
            return ProcessResult(pulled=1, new_timestamps=["2026-05-28 10:00:00.000"])
        return ProcessResult(pulled=0)

    monkeypatch.setattr("backend.agent.aee.reconciler.process_device_logs", fake_pdl)

    rec = AeeDbHistoryReconciler(
        signal_emitter=emitter,
        state_store=store,
        serial="SX",
        job_id=904,
        host_id="HOST",
    )
    assert rec.tick_once() == 1
    assert calls[:2] == [
        "watcher_baseline:904|ml=True|br=True",
        "watcher:aee|ml=True|br=True",
    ]
    assert rec.stats.baseline_entries_total == 1
    assert rec.stats.runtime_entries_total == 0
    assert rec.stats.new_entries_total == 1
    assert emitter.calls[0]["extra"]["aee_ts"] == "2026-05-28 10:00:00.000"
    assert emitter.calls[0]["extra"]["raw_event_type"] == "Native (NE)"
    assert emitter.calls[0]["extra"]["event_subtype"] == "NE"
    assert emitter.calls[0]["extra"]["entry_origin"] == "baseline"
    assert emitter.calls[0]["extra"]["schema_version"] == 2
    assert hasattr(emitter.calls[0]["detected_at"], "tzinfo")

    processed_raw = store.get_state("watcher:aee:SX:aee_exp:processed_entries", "[]")
    assert "db.20" in processed_raw


def test_baseline_snapshot_is_chunked_across_ticks(monkeypatch):
    """baseline 未扫完时应分轮继续,且 hash 未变也不能阻塞后续分片。"""
    emitter = _FakeEmitter()
    store = _MemStore()
    calls: list[tuple[str, Optional[int]]] = []
    baseline_results = [
        ProcessResult(pulled=2, pending_remaining=3),
        ProcessResult(pulled=2, pending_remaining=1),
        ProcessResult(pulled=1, pending_remaining=0),
    ]

    def fake_pdl(*, config, **_):
        calls.append((config.state_key_prefix, config.max_entries_per_run))
        if config.state_key_prefix == "watcher_baseline:905":
            return baseline_results.pop(0)
        return ProcessResult(pulled=0)

    monkeypatch.setattr("backend.agent.aee.reconciler.process_device_logs", fake_pdl)

    holder = {"v": "db.0,CRASH,...\n"}
    rec = AeeDbHistoryReconciler(
        signal_emitter=emitter,
        state_store=store,
        serial="SX",
        job_id=905,
        host_id="HOST",
        shell_fn=_shell_returning(holder),
        baseline_chunk_size=2,
    )

    assert rec.tick_once() == 2
    assert rec._baseline_snapshot_done is False
    assert rec.stats.baseline_entries_total == 2

    assert rec.tick_once() == 2
    assert rec._baseline_snapshot_done is False
    assert rec.stats.baseline_entries_total == 4

    assert rec.tick_once() == 1
    assert rec._baseline_snapshot_done is True
    assert rec.stats.baseline_entries_total == 5
    assert rec.stats.ticks_skipped_unchanged == 2
    assert calls == [
        ("watcher_baseline:905", 2),
        ("watcher:aee", None),
        ("watcher_baseline:905", 2),
        ("watcher_baseline:905", 2),
    ]


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
        baseline_snapshot_enabled=False,
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
        baseline_snapshot_enabled=False,
    )
    rec.tick_once()
    assert rec.stats.signals_dropped == 1
    assert rec.stats.signals_emitted == 0


# ----------------------------------------------------------------------
# 状态键命名空间
# ----------------------------------------------------------------------

def test_state_key_prefix_migrated_to_watcher_namespace():
    """M3:watcher 主链与 processor 默认前缀都应落在 watcher:aee。"""
    from backend.agent.aee.db_history import state_key
    from backend.agent.aee.processor import ProcessConfig

    rec_a = AeeDbHistoryReconciler(
        signal_emitter=_FakeEmitter(),
        state_store=_MemStore(),
        serial="SX",
        job_id=1001,
        host_id="HOST",
        baseline_snapshot_enabled=False,
    )
    rec_b = AeeDbHistoryReconciler(
        signal_emitter=_FakeEmitter(),
        state_store=_MemStore(),
        serial="SX",
        job_id=1002,
        host_id="HOST",
        baseline_snapshot_enabled=False,
    )
    assert rec_a._state_prefix == "watcher:aee"
    assert rec_b._state_prefix == "watcher:aee"
    assert rec_a._cfg.state_key_prefix == "watcher:aee"
    assert rec_b._cfg.state_key_prefix == "watcher:aee"

    default_prefix = ProcessConfig().state_key_prefix
    assert default_prefix == "watcher:aee"
    reconciler_key = state_key("SX", "aee_exp", prefix=rec_a._cfg.state_key_prefix)
    default_key = state_key("SX", "aee_exp")
    assert reconciler_key == "watcher:aee:SX:aee_exp:processed_entries"
    assert default_key == "watcher:aee:SX:aee_exp:processed_entries"


def test_reconciler_no_longer_runtime_migrates_legacy_processed_entries(monkeypatch):
    """legacy state 迁移前移到 agent 启动期后,reconciler 运行期不再改写旧键。"""
    store = _MemStore()
    store.set_state(
        "scan_aee:SX:aee_exp:processed_entries",
        json.dumps(["legacy-line"]),
    )
    store.set_state(
        "watcher:aee:SX:aee_exp:processed_entries",
        json.dumps(["existing-watcher-line"]),
    )

    calls: list[str] = []

    def fake_pdl(*, config, **_):
        calls.append(config.state_key_prefix)
        return ProcessResult(pulled=0)

    monkeypatch.setattr("backend.agent.aee.reconciler.process_device_logs", fake_pdl)
    rec = AeeDbHistoryReconciler(
        signal_emitter=_FakeEmitter(),
        state_store=store,
        serial="SX",
        job_id=1003,
        host_id="HOST",
        shell_fn=lambda cmd, timeout: "db.0,CRASH,...\n",
        baseline_snapshot_enabled=False,
    )

    assert rec.tick_once() == 0
    assert calls == ["watcher:aee"]
    watcher_lines = json.loads(store.get_state("watcher:aee:SX:aee_exp:processed_entries", "[]"))
    legacy_lines = json.loads(store.get_state("scan_aee:SX:aee_exp:processed_entries", "[]"))
    assert watcher_lines == ["existing-watcher-line"]
    assert legacy_lines == ["legacy-line"]


def test_reconciler_no_longer_runtime_migrates_legacy_pending_pull(monkeypatch):
    """pending_pull 迁移也应在启动期完成,运行期 reconciler 不再接管旧键。"""
    store = _MemStore()
    store.set_state(
        "scan_aee:SX:aee_exp:pending_pull",
        json.dumps({"legacy-line": {"db_path": "/data/aee_exp/db.1"}}),
    )
    store.set_state(
        "watcher:aee:SX:aee_exp:pending_pull",
        json.dumps({"existing-line": {"db_path": "/data/aee_exp/db.2"}}),
    )

    monkeypatch.setattr(
        "backend.agent.aee.reconciler.process_device_logs",
        lambda **_: ProcessResult(pulled=0),
    )
    rec = AeeDbHistoryReconciler(
        signal_emitter=_FakeEmitter(),
        state_store=store,
        serial="SX",
        job_id=1004,
        host_id="HOST",
        shell_fn=lambda cmd, timeout: "db.0,CRASH,...\n",
        baseline_snapshot_enabled=False,
    )

    assert rec.tick_once() == 0
    watcher_pending = json.loads(store.get_state("watcher:aee:SX:aee_exp:pending_pull", "{}"))
    legacy_pending = json.loads(store.get_state("scan_aee:SX:aee_exp:pending_pull", "{}"))
    assert watcher_pending == {"existing-line": {"db_path": "/data/aee_exp/db.2"}}
    assert legacy_pending == {"legacy-line": {"db_path": "/data/aee_exp/db.1"}}


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
        shell_fn=lambda cmd, timeout: None,   # 不打真实 adb;None → 保守跑 process
        baseline_snapshot_enabled=False,
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
        shell_fn=lambda cmd, timeout: None,   # 不打真实 adb;None → 保守跑 process
        baseline_snapshot_enabled=False,
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
# D2: db_history hash 跳过 + burst 兼容
# ----------------------------------------------------------------------

def _shell_returning(content_holder: Dict[str, Any]):
    """构造 shell_fn:对 `cat .../db_history` 返回 content_holder['v'],其余空串。"""
    def _shell(cmd: str, timeout: int) -> Optional[str]:
        if "db_history" in cmd:
            return content_holder["v"]
        return ""
    return _shell


def test_tick_skips_process_when_db_history_hash_unchanged(monkeypatch):
    """D2: db_history 内容未变 → 跳过 process_device_logs,计 ticks_skipped_unchanged。"""
    emitter = _FakeEmitter()
    store = _MemStore()
    calls = {"pdl": 0}

    def fake_pdl(*, on_new_entry=None, **_):
        calls["pdl"] += 1
        return ProcessResult(pulled=0)

    monkeypatch.setattr("backend.agent.aee.reconciler.process_device_logs", fake_pdl)

    holder = {"v": "db.0,CRASH,...\n"}
    rec = AeeDbHistoryReconciler(
        signal_emitter=emitter,
        state_store=store,
        serial="SX",
        job_id=2001,
        host_id="HOST",
        shell_fn=_shell_returning(holder),
        baseline_snapshot_enabled=False,
    )

    # 第一轮:cache 空 → hash 变化 → process 被调
    rec.tick_once()
    assert calls["pdl"] == 1
    assert rec.stats.ticks_skipped_unchanged == 0

    # 第二轮:内容一致 → 跳过 process
    rec.tick_once()
    assert calls["pdl"] == 1, "hash 未变时不应再调 process_device_logs"
    assert rec.stats.ticks_skipped_unchanged == 1
    assert rec._last_had_new_candidate is False


def test_hash_unchanged_skip_does_not_reset_burst(monkeypatch):
    """D2: hash 未变跳过的轮次只递减 burst,不重置(模拟 _run 状态机)。"""
    emitter = _FakeEmitter()
    store = _MemStore()
    monkeypatch.setattr(
        "backend.agent.aee.reconciler.process_device_logs",
        lambda *, on_new_entry=None, **_: ProcessResult(pulled=0),
    )
    holder = {"v": "same\n"}
    rec = AeeDbHistoryReconciler(
        signal_emitter=emitter, state_store=store, serial="SX",
        job_id=2002, host_id="HOST", burst_rounds=5,
        shell_fn=_shell_returning(holder),
        baseline_snapshot_enabled=False,
    )
    rec.tick_once()                       # 首轮:hash 变化 → process
    rec._burst_remaining = 3              # 假装处于 burst 中
    rec.tick_once()                       # 第二轮:hash 未变 → 跳过
    assert rec.stats.ticks_skipped_unchanged == 1
    # 复制 _run 决策:跳过轮 _last_had_new_candidate=False → 递减
    with rec._state_lock:
        if rec._last_had_new_candidate:
            rec._burst_remaining = rec._burst_rounds
        elif rec._burst_remaining > 0:
            rec._burst_remaining -= 1
    assert rec._burst_remaining == 2, "未变跳过应递减而非重置到 5"


def test_hash_change_triggers_burst_even_if_pulled_zero(monkeypatch):
    """D2: hash 变化即视为新行候选,即便 process 本轮 pulled=0(已被 patrol 抢先 pull)也触发 burst。"""
    emitter = _FakeEmitter()
    store = _MemStore()
    monkeypatch.setattr(
        "backend.agent.aee.reconciler.process_device_logs",
        lambda *, on_new_entry=None, **_: ProcessResult(pulled=0),
    )
    holder = {"v": "v1\n"}
    rec = AeeDbHistoryReconciler(
        signal_emitter=emitter, state_store=store, serial="SX",
        job_id=2003, host_id="HOST", burst_rounds=5,
        shell_fn=_shell_returning(holder),
        baseline_snapshot_enabled=False,
    )
    # 首轮:cache 空 → hash 变化 → process(pulled=0) → 仍是新行候选
    n1 = rec.tick_once()
    assert n1 == 0
    assert rec._last_had_new_candidate is True

    # 内容继续变化 → 仍触发 burst 候选
    holder["v"] = "v1\nv2\n"
    n2 = rec.tick_once()
    assert n2 == 0
    assert rec._last_had_new_candidate is True

    # 复制 _run 决策 → burst 应被设满
    with rec._state_lock:
        if rec._last_had_new_candidate:
            rec._burst_remaining = rec._burst_rounds
    assert rec._burst_remaining == 5


def test_unreadable_db_history_runs_process_conservatively(monkeypatch):
    """D2: cat 返回 None(不可读)→ 无法判定 → 保守跑 process(不跳过)。"""
    emitter = _FakeEmitter()
    store = _MemStore()
    calls = {"pdl": 0}

    def fake_pdl(*, on_new_entry=None, **_):
        calls["pdl"] += 1
        return ProcessResult(pulled=0)

    monkeypatch.setattr("backend.agent.aee.reconciler.process_device_logs", fake_pdl)

    def shell_none(cmd: str, timeout: int) -> Optional[str]:
        return None  # adb 不可用

    rec = AeeDbHistoryReconciler(
        signal_emitter=emitter, state_store=store, serial="SX",
        job_id=2004, host_id="HOST", shell_fn=shell_none,
        baseline_snapshot_enabled=False,
    )
    rec.tick_once()
    rec.tick_once()
    assert calls["pdl"] == 2, "不可读时每轮都应保守跑 process"
    assert rec.stats.ticks_skipped_unchanged == 0


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
        shell_fn=lambda cmd, timeout: None,   # 不打真实 adb
        baseline_snapshot_enabled=False,
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
        baseline_snapshot_enabled=False,
    )
    stats = rec.stop(timeout=0.5)
    assert isinstance(stats, ReconcilerStats)
    assert stats.ticks_total == 0


def test_stop_joins_reconciler_thread_when_default_adb_shell_is_blocked(monkeypatch):
    """stop() 必须让后台线程真正退出,不能在默认 adb shell 卡住时直接带着活线程返回。"""
    emitter = _FakeEmitter()
    store = _MemStore()
    monkeypatch.setattr(
        "backend.agent.aee.reconciler.process_device_logs",
        lambda *, on_new_entry=None, **_: ProcessResult(pulled=0),
    )

    def slow_run(argv, **kwargs):
        time.sleep(1.0)
        return subprocess.CompletedProcess(args=argv, returncode=0, stdout="", stderr="")

    class _BlockingPopen:
        def __init__(self, argv, **kwargs):
            self.argv = argv
            self.returncode = None
            self.terminated = False
            self.killed = False

        def poll(self):
            return self.returncode

        def terminate(self):
            self.terminated = True
            self.returncode = -15

        def kill(self):
            self.killed = True
            self.returncode = -9

        def wait(self, timeout=None):
            deadline = time.time() + (timeout or 0)
            while self.returncode is None and time.time() < deadline:
                time.sleep(0.01)
            if self.returncode is None:
                raise subprocess.TimeoutExpired(self.argv, timeout)
            return self.returncode

        def communicate(self, timeout=None):
            if self.returncode is None:
                if timeout is not None:
                    time.sleep(min(float(timeout), 0.01))
                    raise subprocess.TimeoutExpired(self.argv, timeout)
                while self.returncode is None:
                    time.sleep(0.01)
            return ("", "")

    fake_subprocess = type("FakeSubprocess", (), {})()
    fake_subprocess.PIPE = object()
    fake_subprocess.TimeoutExpired = subprocess.TimeoutExpired
    fake_subprocess._instances = []

    def _fake_popen(argv, **kwargs):
        proc = _BlockingPopen(argv, **kwargs)
        fake_subprocess._instances.append(proc)
        return proc

    fake_subprocess.Popen = _fake_popen

    monkeypatch.setattr("backend.agent.aee.mobilelog.subprocess.run", slow_run)
    monkeypatch.setattr(
        "backend.agent.aee.reconciler.subprocess", fake_subprocess, raising=False,
    )

    rec = AeeDbHistoryReconciler(
        signal_emitter=emitter,
        state_store=store,
        serial="SX",
        job_id=1203,
        host_id="HOST",
        baseline_snapshot_enabled=False,
    )
    rec.start()
    deadline = time.time() + 1.0
    while time.time() < deadline:
        instances = getattr(fake_subprocess, "_instances", [])
        if instances:
            break
        time.sleep(0.01)

    rec.stop(timeout=0.2)

    assert rec._thread is not None
    assert rec._thread.is_alive() is False, "stop 返回时不应遗留存活的 reconciler 线程"
    assert fake_subprocess._instances, "默认 adb shell 路径应创建可中断的子进程"
    assert fake_subprocess._instances[0].terminated or fake_subprocess._instances[0].killed


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
        baseline_snapshot_enabled=False,
    )
    assert rec._baseline == 30.0
    assert rec._burst == 5.0
    assert rec._burst_rounds == 8
