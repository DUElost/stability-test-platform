"""#10 — step_trace_cache prune 测试.

覆盖:
- LocalDB.prune_acked_step_traces 行为(只删 acked=1 + dead_letter=0,留 keep_recent)
- 未 ack / 死信行不被 prune
- StepTraceUploader 在 N tick 后触发 prune,异常被吞不影响主循环
- snapshot_metrics 新增 pruned_total
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from backend.agent.registry.local_db import LocalDB
from backend.agent.step_trace_uploader import StepTraceUploader


@pytest.fixture
def local_db(tmp_path):
    db = LocalDB()
    db.initialize(str(tmp_path / "agent.sqlite"))
    yield db
    db.close()


# ── LocalDB.prune_acked_step_traces ─────────────────────────────────────


def _save_and_ack(db, *, job_id, step_id, event_type="STARTED") -> int:
    tid = db.save_step_trace(
        job_id=job_id, step_id=step_id, stage="init",
        event_type=event_type, status="RUNNING",
    )
    db.mark_acked(tid)
    return tid


class TestPruneAckedStepTraces:
    def test_returns_zero_when_table_empty(self, local_db):
        assert local_db.prune_acked_step_traces() == 0

    def test_deletes_acked_keeping_recent(self, local_db):
        ids = [_save_and_ack(local_db, job_id=1, step_id=f"s{i}") for i in range(10)]
        deleted = local_db.prune_acked_step_traces(keep_recent=3)
        assert deleted == 7

        remaining = {row["id"] for row in local_db._conn.execute(
            "SELECT id FROM step_trace_cache"
        ).fetchall()}
        # 保留最大 3 个 id(最近)
        assert remaining == set(ids[-3:])

    def test_does_not_touch_unacked(self, local_db):
        acked_ids = [_save_and_ack(local_db, job_id=1, step_id=f"s{i}") for i in range(5)]
        unacked_ids = [
            local_db.save_step_trace(
                job_id=2, step_id=f"u{i}", stage="init",
                event_type="STARTED", status="RUNNING",
            )
            for i in range(3)
        ]

        deleted = local_db.prune_acked_step_traces(keep_recent=0)
        # 5 acked 都删,3 unacked 保留
        assert deleted == 5

        remaining = {row["id"] for row in local_db._conn.execute(
            "SELECT id FROM step_trace_cache"
        ).fetchall()}
        assert remaining == set(unacked_ids)

    def test_keeps_dead_letter_rows_even_when_acked(self, local_db):
        """死信行 dead_letter=1 + acked=1 也必须留下供审计。"""
        tid_dl = _save_and_ack(local_db, job_id=1, step_id="dl")
        local_db.mark_step_trace_dead_letter(tid_dl, "perm")

        acked_ids = [_save_and_ack(local_db, job_id=2, step_id=f"a{i}") for i in range(5)]

        deleted = local_db.prune_acked_step_traces(keep_recent=0)
        # 5 普通 acked 被删,死信行留下
        assert deleted == 5

        remaining = {row["id"] for row in local_db._conn.execute(
            "SELECT id FROM step_trace_cache"
        ).fetchall()}
        assert tid_dl in remaining
        for aid in acked_ids:
            assert aid not in remaining

    def test_keep_recent_counts_dead_letter_separately(self, local_db):
        """dead_letter=1 行不占用 keep_recent 名额(它们用单独逻辑保留)。"""
        # 1 死信 + 5 普通 acked
        tid_dl = _save_and_ack(local_db, job_id=1, step_id="dl")
        local_db.mark_step_trace_dead_letter(tid_dl, "perm")
        normal_ids = [_save_and_ack(local_db, job_id=2, step_id=f"n{i}") for i in range(5)]

        deleted = local_db.prune_acked_step_traces(keep_recent=2)
        # 5 - 2 = 3 普通行被删,死信不动
        assert deleted == 3

        remaining = {row["id"] for row in local_db._conn.execute(
            "SELECT id FROM step_trace_cache"
        ).fetchall()}
        assert tid_dl in remaining
        # 最新 2 个普通行保留
        assert set(normal_ids[-2:]) <= remaining


# ── StepTraceUploader 调度 ──────────────────────────────────────────────


class TestUploaderPruneScheduling:
    def test_maybe_prune_only_fires_after_n_ticks(self):
        db = MagicMock()
        db.prune_acked_step_traces.return_value = 0
        uploader = StepTraceUploader("http://srv", db)
        uploader._PRUNE_EVERY_N_TICKS = 3

        for _ in range(2):
            uploader._maybe_prune()
        assert db.prune_acked_step_traces.call_count == 0
        uploader._maybe_prune()
        assert db.prune_acked_step_traces.call_count == 1
        # 第 4 次到 5 次又不触发,第 6 次再触发
        uploader._maybe_prune()
        uploader._maybe_prune()
        assert db.prune_acked_step_traces.call_count == 1
        uploader._maybe_prune()
        assert db.prune_acked_step_traces.call_count == 2

    def test_pruned_count_accumulates_into_metric(self):
        db = MagicMock()
        db.prune_acked_step_traces.side_effect = [7, 0, 3]
        uploader = StepTraceUploader("http://srv", db)
        uploader._PRUNE_EVERY_N_TICKS = 1

        uploader._maybe_prune()
        assert uploader._pruned_total == 7
        uploader._maybe_prune()
        assert uploader._pruned_total == 7  # 0 不累加
        uploader._maybe_prune()
        assert uploader._pruned_total == 10

    def test_prune_keep_recent_arg_passed_through(self):
        db = MagicMock()
        db.prune_acked_step_traces.return_value = 0
        uploader = StepTraceUploader("http://srv", db)
        uploader._PRUNE_EVERY_N_TICKS = 1
        uploader._PRUNE_KEEP_RECENT = 42

        uploader._maybe_prune()
        db.prune_acked_step_traces.assert_called_once_with(keep_recent=42)

    def test_prune_exception_does_not_crash(self):
        db = MagicMock()
        db.prune_acked_step_traces.side_effect = RuntimeError("disk full")
        uploader = StepTraceUploader("http://srv", db)
        uploader._PRUNE_EVERY_N_TICKS = 1

        # 不应抛
        uploader._maybe_prune()
        # 计数器被消费(reset 到 0),不会一直卡在阈值上
        assert uploader._ticks_since_prune == 0
        # 失败时不更新 pruned_total
        assert uploader._pruned_total == 0


# ── LocalDB-level uploader 集成(端到端,无 mock DB) ────────────────────


def test_uploader_prune_against_real_localdb(local_db):
    """uploader 的 prune 路径接到真实 LocalDB 上 — 端到端冒烟。"""
    # 30 行 acked + 5 行未 acked + 2 行死信
    for i in range(30):
        tid = local_db.save_step_trace(
            job_id=1, step_id=f"a{i}", stage="init",
            event_type="STARTED", status="RUNNING",
        )
        local_db.mark_acked(tid)
    for i in range(5):
        local_db.save_step_trace(
            job_id=2, step_id=f"u{i}", stage="init",
            event_type="STARTED", status="RUNNING",
        )
    for i in range(2):
        tid = local_db.save_step_trace(
            job_id=3, step_id=f"d{i}", stage="init",
            event_type="STARTED", status="RUNNING",
        )
        local_db.mark_acked(tid)
        local_db.mark_step_trace_dead_letter(tid, "perm")

    uploader = StepTraceUploader("http://srv", local_db)
    uploader._PRUNE_EVERY_N_TICKS = 1
    uploader._PRUNE_KEEP_RECENT = 5
    uploader._maybe_prune()

    # 30 普通 acked - 5 留存 = 25 删
    assert uploader._pruned_total == 25
    rows = local_db._conn.execute(
        "SELECT COUNT(*) AS c FROM step_trace_cache"
    ).fetchone()
    # 5 留存 + 5 未 ack + 2 死信 = 12
    assert rows["c"] == 12
