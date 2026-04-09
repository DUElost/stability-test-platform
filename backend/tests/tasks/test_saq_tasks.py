"""Tests for SAQ task functions and worker lifecycle."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# post_completion_task
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_completion_task_calls_run():
    """post_completion_task delegates to run_post_completion_async."""
    with patch(
        "backend.services.post_completion.run_post_completion_async"
    ) as mock_run:
        from backend.tasks.saq_tasks import post_completion_task

        await post_completion_task({}, job_id=42)
        mock_run.assert_called_once_with(42)


@pytest.mark.asyncio
async def test_post_completion_task_reraises():
    """post_completion_task bubbles up exceptions for SAQ retry."""
    with patch(
        "backend.services.post_completion.run_post_completion_async",
        side_effect=RuntimeError("db gone"),
    ):
        from backend.tasks.saq_tasks import post_completion_task

        with pytest.raises(RuntimeError, match="db gone"):
            await post_completion_task({}, job_id=99)


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
    """enqueue_sync logs warning and drops when SAQ is not initialised."""
    import backend.tasks.saq_worker as mod

    original_queue = mod._queue
    original_loop = mod._loop
    try:
        mod._queue = None
        mod._loop = None
        # Should not raise
        mod.enqueue_sync("post_completion_task", job_id=1)
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
