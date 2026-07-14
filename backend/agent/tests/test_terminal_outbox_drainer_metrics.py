"""Terminal outbox drainer backlog metrics."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from requests import HTTPError

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
