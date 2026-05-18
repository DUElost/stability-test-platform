"""审计 Agent #6 — step_trace_uploader 退避 + 死信回归覆盖。

覆盖:
- step_trace_cache schema 增列 attempts/last_error/dead_letter 的 idempotent ALTER
- bump_step_trace_attempt 累计 + 返回新值
- mark_step_trace_dead_letter 后 get_unacked_traces 过滤
- 5xx HTTP 错误下 _bump_or_dead_letter_batch 累计 + 超 _MAX_ATTEMPTS 自动死信
- 网络异常下同样路径
- _advance_backoff 指数退避并被成功 upload 重置
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
import requests

from backend.agent.registry.local_db import LocalDB
from backend.agent.step_trace_uploader import StepTraceUploader


# ── LocalDB schema + helpers ───────────────────────────────────────────────


@pytest.fixture
def local_db(tmp_path):
    db = LocalDB()
    db.initialize(str(tmp_path / "agent.sqlite"))
    yield db
    db.close()


def test_step_trace_cache_schema_includes_attempts_columns(local_db):
    cols = {
        row["name"]
        for row in local_db._conn.execute(
            "PRAGMA table_info(step_trace_cache)"
        ).fetchall()
    }
    assert "attempts" in cols
    assert "last_error" in cols
    assert "dead_letter" in cols


def test_ensure_step_trace_schema_is_idempotent(local_db):
    db_path = local_db._db_path
    local_db.close()

    db2 = LocalDB()
    db2.initialize(db_path)
    try:
        cols = {
            row["name"]
            for row in db2._conn.execute(
                "PRAGMA table_info(step_trace_cache)"
            ).fetchall()
        }
        assert {"attempts", "last_error", "dead_letter"} <= cols
    finally:
        db2.close()


def test_bump_step_trace_attempt_returns_new_count(local_db):
    tid = local_db.save_step_trace(
        job_id=1, step_id="s1", stage="init",
        event_type="STARTED", status="RUNNING",
    )
    assert local_db.bump_step_trace_attempt(tid, "first error") == 1
    assert local_db.bump_step_trace_attempt(tid, "second error") == 2
    row = local_db._conn.execute(
        "SELECT attempts, last_error FROM step_trace_cache WHERE id = ?",
        (tid,),
    ).fetchone()
    assert row["attempts"] == 2
    assert row["last_error"] == "second error"


def test_mark_step_trace_dead_letter_excludes_from_unacked(local_db):
    tid1 = local_db.save_step_trace(
        job_id=1, step_id="s1", stage="init",
        event_type="STARTED", status="RUNNING",
    )
    tid2 = local_db.save_step_trace(
        job_id=1, step_id="s2", stage="init",
        event_type="STARTED", status="RUNNING",
    )
    local_db.mark_step_trace_dead_letter(tid1, "permanent")

    pending = local_db.get_unacked_traces()
    assert [t["id"] for t in pending] == [tid2]

    dl = local_db.get_step_trace_dead_letters()
    assert [d["id"] for d in dl] == [tid1]
    assert dl[0]["last_error"] == "permanent"


# ── Uploader retry + dead-letter ────────────────────────────────────────────


class _FakeDB:
    """In-memory stand-in supporting attempts / dead_letter bookkeeping."""

    def __init__(self, traces):
        self._traces = {t["id"]: dict(t, attempts=0, dead_letter=0) for t in traces}
        self.acked = []
        self.dead_letters = []

    def get_unacked_traces(self, after_id=0):
        return [
            t for t in sorted(self._traces.values(), key=lambda x: x["id"])
            if t["id"] > after_id and t["id"] not in self.acked
            and not t.get("dead_letter")
        ]

    def mark_acked(self, trace_id):
        self.acked.append(trace_id)

    def bump_step_trace_attempt(self, trace_id, error):
        t = self._traces[trace_id]
        t["attempts"] += 1
        t["last_error"] = error
        return t["attempts"]

    def mark_step_trace_dead_letter(self, trace_id, error):
        self._traces[trace_id]["dead_letter"] = 1
        self.dead_letters.append(trace_id)


def _trace(i):
    return {
        "id": i,
        "job_id": 100 + i,
        "step_id": "s" + str(i),
        "stage": "init",
        "event_type": "FAILED",
        "status": "FAILED",
        "output": None,
        "error_message": None,
        "original_ts": "2026-05-18T00:00:00+00:00",
        "fencing_token": str(100 + i) + ":1",
    }


class _Resp:
    def __init__(self, status_code):
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)


def test_5xx_bumps_attempts_and_raises():
    db = _FakeDB([_trace(1), _trace(2)])
    uploader = StepTraceUploader("http://srv", db)

    with patch(
        "backend.agent.step_trace_uploader.requests.post",
        return_value=_Resp(503),
    ):
        with pytest.raises(requests.HTTPError):
            uploader._upload_once()

    assert db._traces[1]["attempts"] == 1
    assert db._traces[2]["attempts"] == 1
    assert uploader._failed_total == 2
    assert uploader._dead_letter_total == 0


def test_network_error_bumps_attempts_and_raises():
    db = _FakeDB([_trace(1)])
    uploader = StepTraceUploader("http://srv", db)

    with patch(
        "backend.agent.step_trace_uploader.requests.post",
        side_effect=requests.ConnectionError("no route"),
    ):
        with pytest.raises(requests.RequestException):
            uploader._upload_once()

    assert db._traces[1]["attempts"] == 1
    assert uploader._failed_total == 1


def test_dead_letter_triggers_after_max_attempts():
    db = _FakeDB([_trace(1)])
    uploader = StepTraceUploader("http://srv", db)
    uploader._MAX_ATTEMPTS = 3

    with patch(
        "backend.agent.step_trace_uploader.requests.post",
        return_value=_Resp(503),
    ):
        for _ in range(3):
            with pytest.raises(requests.HTTPError):
                uploader._upload_once()

    assert db._traces[1]["attempts"] == 3
    assert db._traces[1]["dead_letter"] == 1
    assert uploader._dead_letter_total == 1

    with patch(
        "backend.agent.step_trace_uploader.requests.post",
        return_value=_Resp(503),
    ) as mock_post:
        uploaded = uploader._upload_once()
    assert uploaded == 0
    assert mock_post.call_count == 0


def test_advance_backoff_doubles_until_cap():
    db = _FakeDB([])
    uploader = StepTraceUploader("http://srv", db, interval=5.0)
    uploader._BACKOFF_MAX = 60.0

    uploader._advance_backoff()
    assert uploader._current_backoff == 10.0
    uploader._advance_backoff()
    assert uploader._current_backoff == 20.0
    uploader._advance_backoff()
    assert uploader._current_backoff == 40.0
    uploader._advance_backoff()
    assert uploader._current_backoff == 60.0
    uploader._advance_backoff()
    assert uploader._current_backoff == 60.0


def test_snapshot_metrics_returns_atomic_view():
    db = _FakeDB([])
    uploader = StepTraceUploader("http://srv", db)
    uploader._uploaded_total = 5
    uploader._failed_total = 2
    uploader._dead_letter_total = 1
    uploader._current_backoff = 12.5

    snap = uploader.snapshot_metrics()
    assert snap == {
        "uploaded_total": 5,
        "failed_total": 2,
        "dead_letter_total": 1,
        "current_backoff": 12.5,
    }


def test_resolve_rejected_batch_bumps_non_4xx_remaining():
    db = _FakeDB([_trace(1), _trace(2), _trace(3)])
    uploader = StepTraceUploader("http://srv", db)

    def fake_post(url, json, headers, timeout):
        if len(json) > 1:
            return _Resp(409)
        if json[0]["job_id"] == 101:
            return _Resp(200)
        if json[0]["job_id"] == 102:
            return _Resp(503)
        return _Resp(200)

    with patch(
        "backend.agent.step_trace_uploader.requests.post",
        side_effect=fake_post,
    ):
        resolved = uploader._upload_once()

    assert resolved == 1
    assert db.acked == [1]
    assert db._traces[2]["attempts"] == 1
    assert db._traces[3]["attempts"] == 1
