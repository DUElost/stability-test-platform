"""Terminal outbox drainer backlog metrics."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

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
