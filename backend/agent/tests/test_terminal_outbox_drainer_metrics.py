"""Terminal outbox drainer backlog metrics."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from requests import HTTPError, Response

from backend.agent.outbox_drainer import OutboxDrainThread


def test_snapshot_metrics_tracks_pending_backlog():
    local_db = MagicMock()
    local_db.count_pending_terminals.return_value = 3
    local_db.get_pending_terminals.return_value = []
    local_db.prune_acked_terminals.return_value = None

    drainer = OutboxDrainThread("http://127.0.0.1:8000", local_db, interval=15.0)
    drainer._drain_once()

    metrics = drainer.snapshot_metrics()
    assert metrics["pending_backlog"] == 3
    assert metrics["flushed_total"] == 0


def test_snapshot_metrics_increments_flushed_total():
    local_db = MagicMock()
    local_db.count_pending_terminals.return_value = 1
    local_db.get_pending_terminals.return_value = [
        {"job_id": 42, "payload": {"status": "FAILED"}},
    ]
    local_db.prune_acked_terminals.return_value = None

    drainer = OutboxDrainThread("http://127.0.0.1:8000", local_db, interval=15.0)

    with patch("backend.agent.outbox_drainer.requests.post") as post:
        post.return_value.raise_for_status = MagicMock()
        sent = drainer._drain_once()

    assert sent == 1
    assert drainer.snapshot_metrics()["flushed_total"] == 1


def test_terminal_payload_conflict_is_retained_even_when_job_is_terminal():
    local_db = MagicMock()
    local_db.count_pending_terminals.return_value = 1
    local_db.get_pending_terminals.return_value = [
        {"job_id": 42, "payload": {"update": {"status": "FAILED"}}},
    ]
    local_db.prune_acked_terminals.return_value = None
    local_db.bump_terminal_attempt.return_value = 1
    response = MagicMock(status_code=409)
    response.json.return_value = {
        "detail": {
            "code": "TERMINAL_PAYLOAD_CONFLICT",
            "current_status": "COMPLETED",
        }
    }
    response.raise_for_status.side_effect = HTTPError(
        "conflict", response=response,
    )

    drainer = OutboxDrainThread(
        "http://127.0.0.1:8000", local_db, interval=15.0,
    )
    with patch(
        "backend.agent.outbox_drainer.requests.post",
        return_value=response,
    ):
        assert drainer._drain_once() == 0

    local_db.ack_terminal.assert_not_called()
    local_db.bump_terminal_attempt.assert_called_once()
    assert drainer.snapshot_metrics()["conflicts_retained_total"] == 1


def test_404_acks_even_when_response_bool_is_false():
    """#729: ``bool(Response)`` is False for 4xx — must still ack gone jobs."""
    local_db = MagicMock()
    local_db.count_pending_terminals.return_value = 1
    local_db.get_pending_terminals.return_value = [
        {"job_id": 976, "payload": {"update": {"status": "FAILED"}}},
    ]
    local_db.prune_acked_terminals.return_value = None

    response = Response()
    response.status_code = 404
    response._content = b'{"detail":"job not found"}'
    response.url = "http://127.0.0.1:8000/api/v1/agent/jobs/976/complete"
    assert not response, "precondition: requests.Response is falsy on 404"

    drainer = OutboxDrainThread(
        "http://127.0.0.1:8000", local_db, interval=15.0,
    )
    with patch(
        "backend.agent.outbox_drainer.requests.post",
        return_value=response,
    ):
        assert drainer._drain_once() == 0

    local_db.ack_terminal.assert_called_once_with(976)
    local_db.bump_terminal_attempt.assert_not_called()


# ---------------------------------------------------------------------------
# #762：409 三分支真实 falsy Response 回归（旧测试用 MagicMock，bool 恒 True，
# 旧的真值判断写法也能通过 —— 这正是 409 死代码漏网约 4 个月的原因）
# ---------------------------------------------------------------------------


def _real_conflict_response(body: dict) -> Response:
    """构造真实 requests.Response（4xx 下 bool=False）并带 JSON body。"""
    response = Response()
    response.status_code = 409
    response._content = json.dumps(body).encode("utf-8")
    response.url = "http://127.0.0.1:8000/api/v1/agent/jobs/42/complete"
    assert not response, "precondition: requests.Response is falsy on 409"
    return response


def _drain_with_response(local_db, response) -> int:
    drainer = OutboxDrainThread("http://127.0.0.1:8000", local_db, interval=15.0)
    with patch(
        "backend.agent.outbox_drainer.requests.post", return_value=response,
    ):
        return drainer._drain_once(), drainer


def test_409_payload_conflict_retained_with_real_falsy_response():
    """TERMINAL_PAYLOAD_CONFLICT → retain（非 ack），falsy Response 下同样成立。"""
    local_db = MagicMock()
    local_db.count_pending_terminals.return_value = 1
    local_db.get_pending_terminals.return_value = [
        {"job_id": 42, "payload": {"update": {"status": "FAILED"}}},
    ]
    local_db.prune_acked_terminals.return_value = None

    response = _real_conflict_response({
        "detail": {
            "code": "TERMINAL_PAYLOAD_CONFLICT",
            "current_status": "COMPLETED",
        }
    })
    sent, drainer = _drain_with_response(local_db, response)

    assert sent == 0
    local_db.ack_terminal.assert_not_called()
    local_db.bump_terminal_attempt.assert_called_once()
    assert drainer.snapshot_metrics()["conflicts_retained_total"] == 1


def test_409_unstructured_retained_with_real_falsy_response():
    """非结构化 409（detail 为字符串）→ retain，不得误 ack。"""
    local_db = MagicMock()
    local_db.count_pending_terminals.return_value = 1
    local_db.get_pending_terminals.return_value = [
        {"job_id": 42, "payload": {"update": {"status": "FAILED"}}},
    ]
    local_db.prune_acked_terminals.return_value = None

    response = _real_conflict_response({"detail": "plain-text conflict"})
    sent, drainer = _drain_with_response(local_db, response)

    assert sent == 0
    local_db.ack_terminal.assert_not_called()
    local_db.bump_terminal_attempt.assert_called_once()
    assert drainer.snapshot_metrics()["conflicts_retained_total"] == 1


def test_409_ackable_current_status_acks_with_real_falsy_response():
    """409 + current_status ∈ ACKABLE → 作业已终态，ack 下台。"""
    local_db = MagicMock()
    local_db.count_pending_terminals.return_value = 1
    local_db.get_pending_terminals.return_value = [
        {"job_id": 42, "payload": {"update": {"status": "FAILED"}}},
    ]
    local_db.prune_acked_terminals.return_value = None

    response = _real_conflict_response({
        "detail": {
            "code": "STALE_COMPLETION_TOKEN",
            "current_status": "ABORTED",
        }
    })
    sent, drainer = _drain_with_response(local_db, response)

    assert sent == 1
    local_db.ack_terminal.assert_called_once_with(42)
    local_db.bump_terminal_attempt.assert_not_called()
    assert drainer.snapshot_metrics()["conflicts_retained_total"] == 0


def test_non_409_4xx_permanent_rejection_reaches_dead_letter_cap():
    """#762：非 409/404 的 4xx（中心永久拒绝）达到上限 → 死信，不再占队头。"""
    local_db = MagicMock()
    local_db.count_pending_terminals.return_value = 1
    local_db.get_pending_terminals.return_value = [
        {"job_id": 42, "payload": {"update": {"status": "FAILED"}}},
    ]
    local_db.prune_acked_terminals.return_value = None
    local_db.bump_terminal_attempt.return_value = 10  # 上限（#762）

    response = Response()
    response.status_code = 422
    response._content = b'{"detail":"schema rejected"}'
    response.url = "http://127.0.0.1:8000/api/v1/agent/jobs/42/complete"

    sent, drainer = _drain_with_response(local_db, response)

    assert sent == 0
    local_db.ack_terminal.assert_not_called()
    local_db.mark_terminal_dead_letter.assert_called_once()
    assert drainer.snapshot_metrics()["dead_letter_total"] == 1
    assert drainer.snapshot_metrics()["conflicts_retained_total"] == 0
