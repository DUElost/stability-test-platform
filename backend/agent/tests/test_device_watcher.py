"""DeviceLogWatcher 单元测试。

策略：
    - 使用真实 LocalDB（in-memory SQLite）+ 真实 SignalEmitter 验证 emit → outbox 闭环
    - InotifydSource 用 patch("subprocess.Popen") 注入 _FakePopen 喂事件
    - 直接观察 LocalDB.get_pending_log_signals() 验证 envelope 字段

覆盖：
    - capability=INOTIFYD_ROOT 时启动 source；事件经 batcher → emitter 落 outbox
    - capability=POLLING 时不启动 source（只暴露 batcher，本期视为不接收事件）
    - probe_result.accessible_categories 裁剪订阅路径（不可读分类不入 paths_by_category）
    - stop(drain=True) 把残余 ANR 事件 flush 到 outbox（保证 Job 收尾不丢）
    - SignalEmitter ContractViolation 时不污染主流程（events_dropped 计数）
"""

from __future__ import annotations

import threading
import time
from typing import List, Optional
from unittest.mock import MagicMock, patch

import pytest

from backend.agent.registry.local_db import LocalDB
from backend.agent.watcher.device_watcher import DeviceLogWatcher
from backend.agent.watcher.exceptions import WatcherStartError
from backend.agent.watcher.policy import WatcherPolicy
from backend.agent.watcher.sources import (
    ProbeResult,
    WatcherCapability,
    WatcherEvent,
)
from backend.agent.tests.test_sources import _FakePopen   # 复用 fake


# ----------------------------------------------------------------------
# fixtures
# ----------------------------------------------------------------------

@pytest.fixture
def db(tmp_path):
    db = LocalDB()
    db.initialize(str(tmp_path / "agent.db"))
    yield db
    db.close()


def _probe_all_root() -> ProbeResult:
    return ProbeResult(
        capability=WatcherCapability.INOTIFYD_ROOT,
        accessible_categories=["ANR", "AEE"],
        inaccessible_categories={},
        is_root=True,
        reasons=[],
    )


def _probe_only_anr() -> ProbeResult:
    return ProbeResult(
        capability=WatcherCapability.INOTIFYD_ROOT,
        accessible_categories=["ANR"],
        inaccessible_categories={"AEE": "not_readable"},
        is_root=True,
        reasons=["AEE:not_readable"],
    )


# ----------------------------------------------------------------------
# 端到端：inotifyd → batcher → emitter → outbox
# ----------------------------------------------------------------------

def test_anr_events_flow_to_outbox_via_batch(db):
    """ANR 走聚合：3 条不同文件 → flush → outbox 3 条。"""
    policy = WatcherPolicy(
        batch_interval_seconds=0.3,
        batch_max_events=10,
    )
    watcher = DeviceLogWatcher(
        adb_path="adb", local_db=db,
        host_id="HOST", serial="SX", job_id=701,
        policy=policy,
        capability=WatcherCapability.INOTIFYD_ROOT,
        probe_result=_probe_all_root(),
    )
    lines = [
        "n\t/data/anr\ttrace_a\n",
        "n\t/data/anr\ttrace_b\n",
        "n\t/data/anr\ttrace_c\n",
    ]
    fake = _FakePopen(lines)
    with patch("subprocess.Popen", return_value=fake):
        watcher.start()
        # 等 batch_interval 到达 + flusher 发 emit
        deadline = time.time() + 2.0
        while time.time() < deadline:
            if len(db.get_pending_log_signals()) >= 3:
                break
            time.sleep(0.05)
        stats = watcher.stop(drain=True, timeout=1.0)

    rows = db.get_pending_log_signals()
    assert len(rows) == 3
    envelopes = [r["envelope"] for r in rows]
    cats = sorted(e["category"] for e in envelopes)
    assert cats == ["ANR", "ANR", "ANR"]
    paths = sorted(e["path_on_device"] for e in envelopes)
    assert paths == ["/data/anr/trace_a", "/data/anr/trace_b", "/data/anr/trace_c"]
    # source/sink 字段契约
    assert all(e["source"] == "inotifyd" for e in envelopes)
    assert all(e["host_id"] == "HOST" and e["device_serial"] == "SX" for e in envelopes)
    assert all(e["job_id"] == 701 for e in envelopes)
    # seq_no 单调
    assert sorted(e["seq_no"] for e in envelopes) == [1, 2, 3]
    # stats
    assert stats.events_total == 3
    assert stats.signals_emitted == 3
    assert stats.batch_emits >= 1
    assert stats.immediate_emits == 0


def test_aee_events_immediate_to_outbox(db):
    """AEE 直通：每条立即 emit。"""
    policy = WatcherPolicy(batch_interval_seconds=10.0, batch_max_events=100)
    watcher = DeviceLogWatcher(
        adb_path="adb", local_db=db,
        host_id="HOST", serial="SY", job_id=702,
        policy=policy,
        capability=WatcherCapability.INOTIFYD_ROOT,
        probe_result=_probe_all_root(),
    )
    lines = [
        "n\t/data/aee_exp\tdb.0.0\n",
        "n\t/data/aee_exp\tdb.0.1\n",
    ]
    fake = _FakePopen(lines)
    with patch("subprocess.Popen", return_value=fake):
        watcher.start()
        # immediate 在 inotifyd 读线程同步执行 → outbox 立刻有
        deadline = time.time() + 1.5
        while time.time() < deadline and len(db.get_pending_log_signals()) < 2:
            time.sleep(0.02)
        watcher.stop(drain=False, timeout=1.0)

    rows = db.get_pending_log_signals()
    assert len(rows) == 2
    assert all(r["envelope"]["category"] == "AEE" for r in rows)
    assert watcher.stats.immediate_emits == 2
    assert watcher.stats.batch_emits == 0


# ----------------------------------------------------------------------
# capability 路径
# ----------------------------------------------------------------------

def test_polling_capability_does_not_start_inotifyd(db):
    """capability=POLLING 时不创建 InotifydSource（_source is None）。"""
    watcher = DeviceLogWatcher(
        adb_path="adb", local_db=db,
        host_id="HOST", serial="SP", job_id=703,
        policy=WatcherPolicy(),
        capability=WatcherCapability.POLLING,
    )
    assert watcher._source is None
    # 不会调到 subprocess.Popen
    with patch("subprocess.Popen", side_effect=AssertionError("must not be called")):
        watcher.start()
        watcher.stop(drain=False, timeout=0.5)
    assert db.get_pending_log_signals() == []


def test_probe_result_filters_subscribed_paths(db):
    """probe 出 AEE 不可访问 → 订阅时仅 ANR 入 paths_by_category。"""
    watcher = DeviceLogWatcher(
        adb_path="adb", local_db=db,
        host_id="HOST", serial="SF", job_id=704,
        policy=WatcherPolicy(),
        capability=WatcherCapability.INOTIFYD_ROOT,
        probe_result=_probe_only_anr(),
    )
    paths = watcher._build_subscribed_paths()
    assert set(paths.keys()) == {"ANR"}
    assert "AEE" not in paths


# ----------------------------------------------------------------------
# stop drain
# ----------------------------------------------------------------------

def test_stop_drain_flushes_pending_anr(db):
    """ANR 事件还没到 batch_interval/max，stop(drain=True) 必须 flush。"""
    policy = WatcherPolicy(
        batch_interval_seconds=60.0,   # 故意大，避免周期触发
        batch_max_events=100,
    )
    watcher = DeviceLogWatcher(
        adb_path="adb", local_db=db,
        host_id="HOST", serial="SD", job_id=705,
        policy=policy,
        capability=WatcherCapability.INOTIFYD_ROOT,
        probe_result=_probe_all_root(),
    )
    lines = ["n\t/data/anr\tlast_trace\n"]
    fake = _FakePopen(lines)
    with patch("subprocess.Popen", return_value=fake):
        watcher.start()
        # 等事件到 batcher（不等 flush）
        deadline = time.time() + 1.0
        while time.time() < deadline and watcher.stats.events_total < 1:
            time.sleep(0.02)
        # 此时应该还在 pending（未到 interval）
        assert db.get_pending_log_signals() == []
        watcher.stop(drain=True, timeout=1.0)

    rows = db.get_pending_log_signals()
    assert len(rows) == 1, "drain=True 必须把残余 ANR 同步出库"
    assert rows[0]["envelope"]["path_on_device"] == "/data/anr/last_trace"


# ----------------------------------------------------------------------
# stats / signals_count
# ----------------------------------------------------------------------

def test_signals_count_matches_outbox(db):
    """signals_count（emitter.next_seq_preview - 1）应与 outbox 行数一致。"""
    policy = WatcherPolicy(batch_interval_seconds=0.2, batch_max_events=10)
    watcher = DeviceLogWatcher(
        adb_path="adb", local_db=db,
        host_id="HOST", serial="SC", job_id=706,
        policy=policy,
        capability=WatcherCapability.INOTIFYD_ROOT,
        probe_result=_probe_all_root(),
    )
    lines = [
        "n\t/data/aee_exp\tdb.a\n",     # immediate
        "n\t/data/anr\ttrace_x\n",      # batched
        "n\t/data/anr\ttrace_y\n",      # batched
    ]
    fake = _FakePopen(lines)
    with patch("subprocess.Popen", return_value=fake):
        watcher.start()
        deadline = time.time() + 2.0
        while time.time() < deadline and watcher.signals_count < 3:
            time.sleep(0.05)
        watcher.stop(drain=True, timeout=1.0)

    assert watcher.signals_count == 3
    assert len(db.get_pending_log_signals()) == 3


# ----------------------------------------------------------------------
# 风险收口：source 启动失败 → 硬失败 + 回滚 batcher
# ----------------------------------------------------------------------

def test_source_start_failure_raises_watcher_start_error_and_rolls_back_batcher(db):
    """source.start() 抛异常时：
      - DeviceLogWatcher.start() 抛 WatcherStartError(code='source_start_failed')
      - 已启动的 batcher 被回滚（stop drain=False）
      - _started 保持 False（允许 Manager 决策后重试 / 切降级）
    """
    watcher = DeviceLogWatcher(
        adb_path="adb", local_db=db,
        host_id="HOST", serial="SF", job_id=707,
        policy=WatcherPolicy(),
        capability=WatcherCapability.INOTIFYD_ROOT,
        probe_result=_probe_all_root(),
    )
    # Popen 启动直接抛 → InotifydSource.start() 抛 RuntimeError("inotifyd_spawn_failed")
    with patch("subprocess.Popen", side_effect=FileNotFoundError("no adb")):
        with pytest.raises(WatcherStartError) as excinfo:
            watcher.start()

    assert excinfo.value.code == "source_start_failed"
    assert excinfo.value.context["serial"] == "SF"
    assert excinfo.value.context["job_id"] == 707
    assert watcher._started is False, "失败后 _started 必须保持 False"
    # batcher 已回滚：stop(drain=False) → 后台线程不在跑
    assert (
        watcher._batcher._thread is None
        or not watcher._batcher._thread.is_alive()
    ), "batcher 后台线程应被回滚停止"


def test_start_without_source_does_not_raise_on_success(db):
    """capability=POLLING → _source is None → start() 不触碰 subprocess，正常返回。"""
    watcher = DeviceLogWatcher(
        adb_path="adb", local_db=db,
        host_id="HOST", serial="SG", job_id=708,
        policy=WatcherPolicy(),
        capability=WatcherCapability.POLLING,
    )
    watcher.start()  # 不应抛
    assert watcher._started is True
    watcher.stop(drain=False, timeout=0.5)


# ----------------------------------------------------------------------
# M0/PR #2: AEE/VENDOR_AEE emit 旁路在 reconciler 接管时被关闭
# ----------------------------------------------------------------------

def _make_aee_event(filename: str = "db.0.0", category: str = "AEE") -> WatcherEvent:
    from datetime import datetime, timezone
    dir_path = "/data/aee_exp" if category == "AEE" else "/data/vendor/aee_exp"
    return WatcherEvent(
        category=category,
        event_mask="n",
        dir_path=dir_path,
        filename=filename,
        full_path=f"{dir_path}/{filename}",
        detected_at=datetime(2026, 5, 28, 10, 0, 0, tzinfo=timezone.utc),
    )


def test_aee_emit_skipped_when_reconciler_active(db):
    """aee_reconciler_active=True 时,_on_immediate 对 AEE 不再 emit(reconciler 唯一 emit)。"""
    watcher = DeviceLogWatcher(
        adb_path="adb", local_db=db,
        host_id="HOST", serial="SR1", job_id=801,
        policy=WatcherPolicy(),
        capability=WatcherCapability.INOTIFYD_ROOT,
        probe_result=_probe_all_root(),
        aee_reconciler_active=True,
    )
    # 直接驱动 _on_immediate(模拟 batcher 回调),绕过 inotifyd Popen
    watcher._on_immediate(_make_aee_event("db.0.0", "AEE"))
    watcher._on_immediate(_make_aee_event("db.5", "VENDOR_AEE"))

    rows = db.get_pending_log_signals()
    assert rows == [], "reconciler 接管期间 AEE/VENDOR_AEE 不应由 inotifyd 路径 emit"


def test_aee_emit_active_when_reconciler_inactive(db):
    """aee_reconciler_active=False(默认) 时,_on_immediate 对 AEE 仍 emit(旧行为兜底)。"""
    watcher = DeviceLogWatcher(
        adb_path="adb", local_db=db,
        host_id="HOST", serial="SR2", job_id=802,
        policy=WatcherPolicy(),
        capability=WatcherCapability.INOTIFYD_ROOT,
        probe_result=_probe_all_root(),
        # 默认 aee_reconciler_active=False
    )
    watcher._on_immediate(_make_aee_event("db.0.0", "AEE"))

    rows = db.get_pending_log_signals()
    assert len(rows) == 1
    assert rows[0]["envelope"]["category"] == "AEE"
    assert rows[0]["envelope"]["source"] == "inotifyd"


def test_on_pull_done_skips_emit_for_aee_when_reconciler_active(db):
    """_on_pull_done 路径:reconciler 接管时跳 _safe_emit,但 ArtifactUploader 仍可走。"""
    watcher = DeviceLogWatcher(
        adb_path="adb", local_db=db,
        host_id="HOST", serial="SR3", job_id=803,
        policy=WatcherPolicy(),
        capability=WatcherCapability.INOTIFYD_ROOT,
        probe_result=_probe_all_root(),
        aee_reconciler_active=True,
    )
    enrichment = {
        "artifact_uri": "/mnt/nfs/jobs/803/AEE/db.0.0",
        "sha256":       "deadbeef",
        "size_bytes":   1024,
        "first_lines":  "header",
    }
    # 直接驱动 _on_pull_done(模拟 LogPuller 回调)
    watcher._on_pull_done(_make_aee_event("db.0.0", "AEE"), enrichment)

    rows = db.get_pending_log_signals()
    assert rows == [], "reconciler 接管期间 _on_pull_done 不应 emit AEE log_signal"


def test_aee_reconciler_active_does_not_affect_anr(db):
    """开关只影响 AEE/VENDOR_AEE;ANR 仍由 inotifyd 路径正常 emit。"""
    policy = WatcherPolicy(batch_interval_seconds=0.2, batch_max_events=10)
    watcher = DeviceLogWatcher(
        adb_path="adb", local_db=db,
        host_id="HOST", serial="SR4", job_id=804,
        policy=policy,
        capability=WatcherCapability.INOTIFYD_ROOT,
        probe_result=_probe_all_root(),
        aee_reconciler_active=True,
    )
    from datetime import datetime, timezone
    anr_event = WatcherEvent(
        category="ANR",
        event_mask="n",
        dir_path="/data/anr",
        filename="trace_x",
        full_path="/data/anr/trace_x",
        detected_at=datetime(2026, 5, 28, 10, 0, 0, tzinfo=timezone.utc),
    )
    # ANR 走 _on_batch,直接驱动
    watcher._on_batch([anr_event])

    rows = db.get_pending_log_signals()
    assert len(rows) == 1
    assert rows[0]["envelope"]["category"] == "ANR"


def test_emitter_property_exposes_signal_emitter(db):
    """DeviceLogWatcher.emitter 暴露内部 SignalEmitter(供 reconciler 共享 seq_no)。"""
    from backend.agent.watcher.emitter import SignalEmitter
    watcher = DeviceLogWatcher(
        adb_path="adb", local_db=db,
        host_id="HOST", serial="SR5", job_id=805,
        policy=WatcherPolicy(),
        capability=WatcherCapability.POLLING,
    )
    assert isinstance(watcher.emitter, SignalEmitter)
    assert watcher.emitter.job_id == 805
    assert watcher.aee_reconciler_active is False

