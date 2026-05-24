"""Tests for SAQ task functions and worker lifecycle."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# post_completion_task
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_completion_task_calls_run():
    """post_completion_task delegates blocking post-completion via to_thread."""
    with patch(
        "backend.services.post_completion.run_post_completion_async"
    ) as mock_run, patch(
        "asyncio.to_thread",
        new_callable=AsyncMock,
    ) as mock_to_thread:
        from backend.tasks.saq_tasks import post_completion_task

        await post_completion_task({}, job_id=42)
        mock_to_thread.assert_awaited_once_with(mock_run, 42)
        mock_run.assert_not_called()


@pytest.mark.asyncio
async def test_post_completion_task_reraises():
    """post_completion_task bubbles up to_thread exceptions for SAQ retry."""
    with patch(
        "backend.services.post_completion.run_post_completion_async"
    ) as mock_run, patch(
        "asyncio.to_thread",
        new_callable=AsyncMock,
        side_effect=RuntimeError("db gone"),
    ):
        from backend.tasks.saq_tasks import post_completion_task

        with pytest.raises(RuntimeError, match="db gone"):
            await post_completion_task({}, job_id=99)
        mock_run.assert_not_called()


# ---------------------------------------------------------------------------
# send_notification_task
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_notification_task_calls_dispatch():
    """send_notification_task delegates to dispatch_notification via to_thread."""
    with patch(
        "backend.services.notification_service.dispatch_notification"
    ) as mock_dispatch:
        from backend.tasks.saq_tasks import send_notification_task

        ctx_payload = {"run_id": 1, "task_name": "job-1"}
        await send_notification_task(
            {}, event_type="RUN_FAILED", context=ctx_payload
        )
        mock_dispatch.assert_called_once_with("RUN_FAILED", ctx_payload)


@pytest.mark.asyncio
async def test_send_notification_task_reraises():
    """send_notification_task bubbles up exceptions for SAQ retry."""
    with patch(
        "backend.services.notification_service.dispatch_notification",
        side_effect=ConnectionError("smtp down"),
    ):
        from backend.tasks.saq_tasks import send_notification_task

        with pytest.raises(ConnectionError, match="smtp down"):
            await send_notification_task(
                {}, event_type="RUN_COMPLETED", context={}
            )


# ---------------------------------------------------------------------------
# publish_control_command (forward-looking stub)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publish_control_command_succeeds():
    """publish_control_command runs without error (stub)."""
    from backend.tasks.saq_tasks import publish_control_command

    await publish_control_command(
        {}, host_id="host-101", command="abort", payload={"reason": "test"}
    )


# ---------------------------------------------------------------------------
# enqueue_sync bridge
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enqueue_sync_schedules_on_loop():
    """enqueue_sync posts to the stored event loop via run_coroutine_threadsafe."""
    import backend.tasks.saq_worker as mod

    mock_queue = MagicMock()
    mock_queue.enqueue = AsyncMock(return_value=None)

    original_queue = mod._queue
    original_loop = mod._loop
    try:
        mod._queue = mock_queue
        mod._loop = asyncio.get_running_loop()

        await asyncio.to_thread(
            mod.enqueue_sync,
            "post_completion_task",
            key="pc:1",
            job_id=1,
        )

        assert mock_queue.enqueue.called
        job_arg = mock_queue.enqueue.call_args[0][0]
        assert job_arg.function == "post_completion_task"
        assert job_arg.kwargs == {"job_id": 1}
        assert job_arg.key == "pc:1"
    finally:
        mod._queue = original_queue
        mod._loop = original_loop


def test_enqueue_sync_drops_when_not_running():
    """enqueue_sync logs warning and returns False when SAQ is not initialised."""
    import backend.tasks.saq_worker as mod

    original_queue = mod._queue
    original_loop = mod._loop
    try:
        mod._queue = None
        mod._loop = None
        assert mod.enqueue_sync("post_completion_task", job_id=1) is False
    finally:
        mod._queue = original_queue
        mod._loop = original_loop


def test_enqueue_sync_required_raises_when_not_running():
    """required=True surfaces EnqueueSyncError instead of silent drop."""
    import backend.tasks.saq_worker as mod

    original_queue = mod._queue
    original_loop = mod._loop
    try:
        mod._queue = None
        mod._loop = None
        with pytest.raises(mod.EnqueueSyncError, match="SAQ not running"):
            mod.enqueue_sync("precheck_and_dispatch_task", required=True, plan_run_id=1)
    finally:
        mod._queue = original_queue
        mod._loop = original_loop


@pytest.mark.asyncio
async def test_enqueue_sync_required_waits_and_raises_on_enqueue_failure():
    """required=True must surface enqueue failures instead of returning 200."""
    import backend.tasks.saq_worker as mod

    mock_queue = MagicMock()
    mock_queue.enqueue = AsyncMock(side_effect=ConnectionError("redis down"))

    original_queue = mod._queue
    original_loop = mod._loop
    try:
        mod._queue = mock_queue
        mod._loop = asyncio.get_running_loop()

        with pytest.raises(mod.EnqueueSyncError, match="enqueue failed"):
            await asyncio.to_thread(
                mod.enqueue_sync,
                "precheck_and_dispatch_task",
                required=True,
                plan_run_id=1,
            )
    finally:
        mod._queue = original_queue
        mod._loop = original_loop


# ---------------------------------------------------------------------------
# get_queue guard
# ---------------------------------------------------------------------------


def test_get_queue_raises_when_uninitialised():
    """get_queue raises RuntimeError before start_saq_worker."""
    import backend.tasks.saq_worker as mod

    original = mod._queue
    try:
        mod._queue = None
        with pytest.raises(RuntimeError, match="not initialised"):
            mod.get_queue()
    finally:
        mod._queue = original


@pytest.mark.asyncio
async def test_stop_saq_worker_awaits_async_worker_stop():
    """stop_saq_worker awaits Worker.stop() before disconnecting queue."""
    import backend.tasks.saq_worker as mod

    mock_worker = MagicMock()
    mock_worker.stop = AsyncMock(return_value=None)
    mock_queue = MagicMock()
    mock_queue.disconnect = AsyncMock(return_value=None)
    worker_task = asyncio.create_task(asyncio.sleep(0))

    original_worker = mod._worker
    original_worker_task = mod._worker_task
    original_queue = mod._queue
    original_loop = mod._loop
    try:
        mod._worker = mock_worker
        mod._worker_task = worker_task
        mod._queue = mock_queue
        mod._loop = asyncio.get_running_loop()

        await mod.stop_saq_worker()

        mock_worker.stop.assert_awaited_once()
        mock_queue.disconnect.assert_awaited_once()
        assert mod._worker is None
        assert mod._worker_task is None
        assert mod._queue is None
        assert mod._loop is None
    finally:
        if not worker_task.done():
            worker_task.cancel()
        mod._worker = original_worker
        mod._worker_task = original_worker_task
        mod._queue = original_queue
        mod._loop = original_loop


# ---------------------------------------------------------------------------
# SAQ worker start/stop idempotency (ADR-0021 audit)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_saq_worker_is_idempotent(monkeypatch):
    """start_saq_worker is a no-op when the worker is already running."""
    import backend.tasks.saq_worker as mod

    fake_queue = MagicMock()
    fake_queue.connect = AsyncMock()
    fake_worker = MagicMock()
    fake_worker.start = AsyncMock()

    monkeypatch.setattr(mod.Queue, "from_url", lambda *args, **kwargs: fake_queue)
    monkeypatch.setattr(mod, "Worker", lambda *args, **kwargs: fake_worker)

    await mod.start_saq_worker()
    await mod.start_saq_worker()

    fake_queue.connect.assert_awaited_once()


def test_get_saq_job_state_sync_returns_none_when_queue_missing():
    """get_saq_job_state_sync returns None when the SAQ queue is not initialised."""
    import backend.tasks.saq_worker as mod

    original_queue = mod._queue
    original_loop = mod._loop
    try:
        mod._queue = None
        mod._loop = None
        assert mod.get_saq_job_state_sync("precheck:1") is None
    finally:
        mod._queue = original_queue
        mod._loop = original_loop


@pytest.mark.asyncio
async def test_start_saq_worker_recreates_queue_after_stopped_worker(monkeypatch):
    """start_saq_worker disconnects old queue before reconnecting when previous
    worker task has completed."""
    import backend.tasks.saq_worker as mod

    old_queue = MagicMock()
    old_queue.disconnect = AsyncMock()
    fake_queue = MagicMock()
    fake_queue.connect = AsyncMock()
    fake_worker = MagicMock()
    fake_worker.start = AsyncMock()

    # Simulate a completed (done) worker task.
    done_task = asyncio.create_task(asyncio.sleep(0))
    await done_task

    monkeypatch.setattr(mod.Queue, "from_url", lambda *args, **kwargs: fake_queue)
    monkeypatch.setattr(mod, "Worker", lambda *args, **kwargs: fake_worker)
    mod._queue = old_queue
    mod._worker_task = done_task

    await mod.start_saq_worker()

    old_queue.disconnect.assert_awaited_once()
    fake_queue.connect.assert_awaited_once()
