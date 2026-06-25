"""Backend unit tests for scan_task multi-host poll and auto_archive_sweep.

P1-1 (#36): scan_task waits for registered >= n_triggered before breaking.
P1-3 (#38): auto_archive_sweep rate-limits incremental scans by last_scan_at.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# P1-1: scan_task multi-host poll
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scan_task_polls_until_all_hosts_registered():
    """scan_task should keep polling until registered >= n_triggered."""
    from backend.tasks import saq_tasks

    mock_db = MagicMock()
    mock_db.execute.return_value.all.return_value = [
        ("host-1", "ONLINE"),
        ("host-2", "ONLINE"),
    ]
    mock_db.close = MagicMock()

    poll_count = 0

    async def fake_to_thread(fn, *a, **kw):
        nonlocal poll_count
        poll_count += 1
        return "1" if poll_count == 1 else "2"

    saq_tasks.asyncio_sleep = AsyncMock()
    saq_tasks.asyncio_to_thread = AsyncMock(side_effect=fake_to_thread)

    with patch("backend.core.database.SessionLocal", return_value=mock_db), \
         patch("backend.realtime.socketio_server.emit_agent_control", new=AsyncMock()), \
         patch("backend.services.dedup_scan.run_scan_sync"):
        mock_queue = MagicMock()
        mock_queue.enqueue = AsyncMock()
        with patch("backend.tasks.saq_worker.get_queue", return_value=mock_queue), \
             patch("saq.Job", MagicMock()):
            await saq_tasks.scan_task({}, plan_run_id=42, is_final=True)

    assert poll_count == 2


@pytest.mark.asyncio
async def test_scan_task_breaks_on_all_registered_first_poll():
    """scan_task breaks immediately if all hosts registered in first poll."""
    from backend.tasks import saq_tasks

    mock_db = MagicMock()
    mock_db.execute.return_value.all.return_value = [
        ("host-1", "ONLINE"),
        ("host-2", "ONLINE"),
    ]
    mock_db.close = MagicMock()

    to_thread = AsyncMock(return_value="2")
    saq_tasks.asyncio_sleep = AsyncMock()
    saq_tasks.asyncio_to_thread = to_thread

    with patch("backend.core.database.SessionLocal", return_value=mock_db), \
         patch("backend.realtime.socketio_server.emit_agent_control", new=AsyncMock()), \
         patch("backend.services.dedup_scan.run_scan_sync"):
        mock_queue = MagicMock()
        mock_queue.enqueue = AsyncMock()
        with patch("backend.tasks.saq_worker.get_queue", return_value=mock_queue), \
             patch("saq.Job", MagicMock()):
            await saq_tasks.scan_task({}, plan_run_id=42, is_final=True)

    assert to_thread.call_count == 1


@pytest.mark.asyncio
async def test_scan_task_no_hosts_triggered_skips_poll():
    """scan_task skips poll loop when no ONLINE hosts found."""
    from backend.tasks import saq_tasks

    mock_db = MagicMock()
    mock_db.execute.return_value.all.return_value = [
        ("host-1", "OFFLINE"),
    ]
    mock_db.close = MagicMock()

    to_thread = AsyncMock(return_value="1")
    saq_tasks.asyncio_sleep = AsyncMock()
    saq_tasks.asyncio_to_thread = to_thread

    with patch("backend.core.database.SessionLocal", return_value=mock_db), \
         patch("backend.realtime.socketio_server.emit_agent_control", new=AsyncMock()):
        mock_queue = MagicMock()
        mock_queue.enqueue = AsyncMock()
        with patch("backend.tasks.saq_worker.get_queue", return_value=mock_queue), \
             patch("saq.Job", MagicMock()):
            await saq_tasks.scan_task({}, plan_run_id=42, is_final=True)

    to_thread.assert_not_awaited()


# ---------------------------------------------------------------------------
# merge_task → extract_task chain
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_merge_task_enqueues_extract_on_success():
    """merge_task should wait for upload then enqueue extract_task."""
    from backend.tasks import saq_tasks

    wait_upload = AsyncMock(return_value=True)
    with patch("asyncio.to_thread", new=AsyncMock(return_value="ok")), \
         patch.object(saq_tasks, "_wait_for_upload_task", wait_upload):
        mock_queue = MagicMock()
        mock_queue.enqueue = AsyncMock()
        with patch("backend.tasks.saq_worker.get_queue", return_value=mock_queue), \
             patch("saq.Job") as mock_job_cls:
            await saq_tasks.merge_task({}, plan_run_id=42)

    wait_upload.assert_awaited_once_with(42)
    mock_job_cls.assert_called_once()
    assert mock_job_cls.call_args.kwargs["function"] == "extract_task"
    assert mock_job_cls.call_args.kwargs["kwargs"] == {"plan_run_id": 42}
    mock_queue.enqueue.assert_awaited_once()


@pytest.mark.asyncio
async def test_merge_task_skips_extract_when_merge_skipped():
    """merge_task should not wait for upload or enqueue extract when merge skipped."""
    from backend.tasks import saq_tasks

    wait_upload = AsyncMock()
    with patch("asyncio.to_thread", new=AsyncMock(return_value="")), \
         patch.object(saq_tasks, "_wait_for_upload_task", wait_upload):
        mock_queue = MagicMock()
        mock_queue.enqueue = AsyncMock()
        with patch("backend.tasks.saq_worker.get_queue", return_value=mock_queue), \
             patch("saq.Job", MagicMock()):
            await saq_tasks.merge_task({}, plan_run_id=42)

    wait_upload.assert_not_awaited()
    mock_queue.enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_wait_for_upload_task_polls_until_complete():
    """_wait_for_upload_task returns True once upload SAQ job reaches terminal state."""
    from backend.tasks import saq_tasks

    states = [{"status": "active"}, {"status": "complete"}]
    idx = 0

    def fake_get_state(_key: str):
        nonlocal idx
        state = states[min(idx, len(states) - 1)]
        idx += 1
        return state

    saq_tasks.asyncio_sleep = AsyncMock()
    saq_tasks.asyncio_to_thread = AsyncMock(side_effect=lambda fn, *a: fake_get_state(a[0]))

    result = await saq_tasks._wait_for_upload_task(42)

    assert result is True
    assert saq_tasks.asyncio_to_thread.await_count == 2
    saq_tasks.asyncio_sleep.assert_awaited_once()


@pytest.mark.asyncio
async def test_wait_for_upload_task_timeout_still_returns_false():
    """_wait_for_upload_task returns False on timeout (merge may still extract best-effort)."""
    from backend.tasks import saq_tasks

    saq_tasks.asyncio_sleep = AsyncMock()
    saq_tasks.asyncio_to_thread = AsyncMock(return_value={"status": "active"})
    saq_tasks._UPLOAD_WAIT_INTERVAL = 1
    saq_tasks._UPLOAD_WAIT_MAX = 2

    result = await saq_tasks._wait_for_upload_task(42)

    assert result is False
    assert saq_tasks.asyncio_to_thread.await_count >= 1


@pytest.mark.asyncio
async def test_scan_task_enqueues_upload_and_merge_only():
    """scan_task should not enqueue extract_task (chained from merge_task)."""
    from backend.tasks import saq_tasks

    mock_db = MagicMock()
    mock_db.execute.return_value.all.return_value = [("host-1", "ONLINE")]
    mock_db.close = MagicMock()

    saq_tasks.asyncio_sleep = AsyncMock()
    saq_tasks.asyncio_to_thread = AsyncMock(return_value="1")

    with patch("backend.core.database.SessionLocal", return_value=mock_db), \
         patch("backend.realtime.socketio_server.emit_agent_control", new=AsyncMock()), \
         patch("backend.services.dedup_scan.run_scan_sync"):
        mock_queue = MagicMock()
        mock_queue.enqueue = AsyncMock()
        with patch("backend.tasks.saq_worker.get_queue", return_value=mock_queue), \
             patch("saq.Job") as mock_job_cls:
            await saq_tasks.scan_task({}, plan_run_id=42, is_final=True)

    assert mock_job_cls.call_count == 2
    functions = [c.kwargs["function"] for c in mock_job_cls.call_args_list]
    assert functions == ["upload_task", "merge_task"]
    assert "extract_task" not in functions


# ---------------------------------------------------------------------------
# P1-3: auto_archive_sweep rate-limiting + incremental
# ---------------------------------------------------------------------------


def test_auto_archive_sweep_first_scan_is_final():
    """First sweep (no scan artifacts) enqueues with is_final=True."""
    import backend.scheduler.cron_scheduler as mod

    mock_db = MagicMock()
    session_cm = MagicMock()
    session_cm.__enter__ = MagicMock(return_value=mock_db)
    session_cm.__exit__ = MagicMock(return_value=False)
    mock_SessionLocal = MagicMock(return_value=session_cm)

    mock_run = MagicMock()
    mock_run.id = 1
    mock_run.ended_at = datetime.now(timezone.utc) - timedelta(hours=2)
    mock_run.plan.auto_archive_interval_seconds = 3600

    mock_query = MagicMock()
    mock_query.join.return_value = mock_query
    mock_query.filter.return_value = mock_query
    mock_query.all.return_value = [mock_run]
    mock_db.query.return_value = mock_query

    execute_result = MagicMock()
    execute_result.scalar_one.side_effect = [0]
    mock_db.execute.return_value = execute_result

    orig = mod.SessionLocal
    mod.SessionLocal = mock_SessionLocal

    try:
        with patch("backend.services.dedup_scan.enqueue_dedup_terminal_sync") as mock_enqueue:
            mod.auto_archive_sweep()
            mock_enqueue.assert_called_once_with(1, is_final=True)
    finally:
        mod.SessionLocal = orig


def test_auto_archive_sweep_incremental_respects_interval():
    """Incremental scan skipped when last scan is within interval."""
    import backend.scheduler.cron_scheduler as mod

    mock_db = MagicMock()
    session_cm = MagicMock()
    session_cm.__enter__ = MagicMock(return_value=mock_db)
    session_cm.__exit__ = MagicMock(return_value=False)
    mock_SessionLocal = MagicMock(return_value=session_cm)

    mock_run = MagicMock()
    mock_run.id = 2
    mock_run.ended_at = datetime.now(timezone.utc) - timedelta(hours=5)
    mock_run.plan.auto_archive_interval_seconds = 3600

    mock_query = MagicMock()
    mock_query.join.return_value = mock_query
    mock_query.filter.return_value = mock_query
    mock_query.all.return_value = [mock_run]
    mock_db.query.return_value = mock_query

    last_scan_time = datetime.now(timezone.utc) - timedelta(minutes=30)
    execute_result = MagicMock()
    execute_result.scalar_one.side_effect = [1, last_scan_time]
    mock_db.execute.return_value = execute_result

    orig = mod.SessionLocal
    mod.SessionLocal = mock_SessionLocal

    try:
        with patch("backend.services.dedup_scan.enqueue_dedup_terminal_sync") as mock_enqueue:
            mod.auto_archive_sweep()
            mock_enqueue.assert_not_called()
    finally:
        mod.SessionLocal = orig


def test_auto_archive_sweep_incremental_enqueues_after_interval():
    """Incremental scan enqueued when interval elapsed since last scan."""
    import backend.scheduler.cron_scheduler as mod

    mock_db = MagicMock()
    session_cm = MagicMock()
    session_cm.__enter__ = MagicMock(return_value=mock_db)
    session_cm.__exit__ = MagicMock(return_value=False)
    mock_SessionLocal = MagicMock(return_value=session_cm)

    mock_run = MagicMock()
    mock_run.id = 3
    mock_run.ended_at = datetime.now(timezone.utc) - timedelta(hours=5)
    mock_run.plan.auto_archive_interval_seconds = 3600

    mock_query = MagicMock()
    mock_query.join.return_value = mock_query
    mock_query.filter.return_value = mock_query
    mock_query.all.return_value = [mock_run]
    mock_db.query.return_value = mock_query

    last_scan_time = datetime.now(timezone.utc) - timedelta(hours=2)
    execute_result = MagicMock()
    execute_result.scalar_one.side_effect = [2, last_scan_time]
    mock_db.execute.return_value = execute_result

    orig = mod.SessionLocal
    mod.SessionLocal = mock_SessionLocal

    try:
        with patch("backend.services.dedup_scan.enqueue_dedup_terminal_sync") as mock_enqueue:
            mod.auto_archive_sweep()
            mock_enqueue.assert_called_once_with(3, is_final=False)
    finally:
        mod.SessionLocal = orig


def test_auto_archive_sweep_skips_run_before_interval():
    """PlanRun within ended_at + interval is skipped entirely."""
    import backend.scheduler.cron_scheduler as mod

    mock_db = MagicMock()
    session_cm = MagicMock()
    session_cm.__enter__ = MagicMock(return_value=mock_db)
    session_cm.__exit__ = MagicMock(return_value=False)
    mock_SessionLocal = MagicMock(return_value=session_cm)

    mock_run = MagicMock()
    mock_run.id = 4
    mock_run.ended_at = datetime.now(timezone.utc) - timedelta(minutes=30)
    mock_run.plan.auto_archive_interval_seconds = 3600

    mock_query = MagicMock()
    mock_query.join.return_value = mock_query
    mock_query.filter.return_value = mock_query
    mock_query.all.return_value = [mock_run]
    mock_db.query.return_value = mock_query

    orig = mod.SessionLocal
    mod.SessionLocal = mock_SessionLocal

    try:
        with patch("backend.services.dedup_scan.enqueue_dedup_terminal_sync") as mock_enqueue:
            mod.auto_archive_sweep()
            mock_enqueue.assert_not_called()
    finally:
        mod.SessionLocal = orig
