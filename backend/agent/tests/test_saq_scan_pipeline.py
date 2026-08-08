"""Backend unit tests for scan_task multi-host poll and auto_archive_sweep.

P1-1 (#36): scan_task waits until every triggered host has scan artifacts.
P1-3 (#38): auto_archive_sweep rate-limits incremental scans by last_scan_at.
"""
from __future__ import annotations

import contextlib
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# P1-1: scan_task multi-host poll
# ---------------------------------------------------------------------------


def _host_rows_db(rows):
    mock_db = MagicMock()
    mock_db.execute.return_value.all.return_value = rows
    mock_db.close = MagicMock()
    return mock_db


@contextlib.contextmanager
def _scan_task_env(saq_tasks, monkeypatch, mock_db, *, to_thread, scan_sync,
                   hosts_done, record_archive, queue):
    """Patch stack shared by the scan_task poll tests.

    ``asyncio_sleep`` / ``asyncio_to_thread`` go through ``monkeypatch`` so the
    module-level attributes are restored after each test instead of leaking a
    fake sleep into every later test in the session.
    """
    monkeypatch.setattr(saq_tasks, "asyncio_sleep", AsyncMock())
    monkeypatch.setattr(saq_tasks, "asyncio_to_thread", to_thread)
    with patch("backend.core.database.SessionLocal", return_value=mock_db), \
         patch("backend.realtime.socketio_server.emit_agent_control", new=AsyncMock()), \
         patch("backend.services.dedup_scan.run_scan_sync", scan_sync), \
         patch("backend.services.dedup_scan.count_hosts_with_scan_artifacts", hosts_done), \
         patch("backend.services.dedup_scan.record_scan_archive_state", record_archive), \
         patch("backend.tasks.saq_worker.get_queue", return_value=queue), \
         patch("saq.Job") as job_cls:
        yield job_cls


@pytest.mark.asyncio
async def test_scan_task_polls_until_all_hosts_registered(monkeypatch):
    """scan_task keeps polling until every triggered host has artifacts."""
    from backend.tasks import saq_tasks

    scan_sync, hosts_done, record_archive = MagicMock(), MagicMock(), MagicMock()
    polls = 0

    async def fake_to_thread(fn, *a, **kw):
        nonlocal polls
        if fn is scan_sync:
            polls += 1
            return "2"
        if fn is hosts_done:
            # Only host-1 in the first round; host-2 shows up in the second.
            return 1 if polls == 1 else 2
        return fn(*a, **kw)

    queue = MagicMock()
    queue.enqueue = AsyncMock()
    with _scan_task_env(
        saq_tasks, monkeypatch, _host_rows_db([("host-1", "ONLINE"), ("host-2", "ONLINE")]),
        to_thread=AsyncMock(side_effect=fake_to_thread), scan_sync=scan_sync,
        hosts_done=hosts_done, record_archive=record_archive, queue=queue,
    ):
        await saq_tasks.scan_task({}, plan_run_id=42, is_final=True)

    assert polls == 2


@pytest.mark.asyncio
async def test_scan_task_does_not_break_on_one_host_worth_of_files(monkeypatch):
    """File count must not stand in for host coverage.

    Each host uploads two matching ``*_org*.xls`` files, so the old
    ``registered >= n_triggered`` check was satisfied by a single finished host
    and dropped the slower ones from the merge.
    """
    from backend.tasks import saq_tasks

    scan_sync, hosts_done, record_archive = MagicMock(), MagicMock(), MagicMock()
    polls = 0

    async def fake_to_thread(fn, *a, **kw):
        nonlocal polls
        if fn is scan_sync:
            polls += 1
            # host-1's two files land at once — enough to satisfy a file count
            # of 2 against 2 triggered hosts, but only one host is covered.
            return "2" if polls == 1 else "0"
        if fn is hosts_done:
            return 1 if polls < 3 else 2
        return fn(*a, **kw)

    queue = MagicMock()
    queue.enqueue = AsyncMock()
    with _scan_task_env(
        saq_tasks, monkeypatch, _host_rows_db([("host-1", "ONLINE"), ("host-2", "ONLINE")]),
        to_thread=AsyncMock(side_effect=fake_to_thread), scan_sync=scan_sync,
        hosts_done=hosts_done, record_archive=record_archive, queue=queue,
    ):
        await saq_tasks.scan_task({}, plan_run_id=42, is_final=True)

    assert polls == 3
    assert record_archive.call_args.kwargs["hosts_with_artifacts"] == 2


@pytest.mark.asyncio
async def test_scan_task_breaks_on_all_registered_first_poll(monkeypatch):
    """scan_task breaks immediately if all hosts delivered in the first poll."""
    from backend.tasks import saq_tasks

    scan_sync, hosts_done, record_archive = MagicMock(), MagicMock(), MagicMock()

    async def fake_to_thread(fn, *a, **kw):
        if fn is scan_sync:
            return "4"
        if fn is hosts_done:
            return 2
        return fn(*a, **kw)

    to_thread = AsyncMock(side_effect=fake_to_thread)
    queue = MagicMock()
    queue.enqueue = AsyncMock()
    with _scan_task_env(
        saq_tasks, monkeypatch, _host_rows_db([("host-1", "ONLINE"), ("host-2", "ONLINE")]),
        to_thread=to_thread, scan_sync=scan_sync,
        hosts_done=hosts_done, record_archive=record_archive, queue=queue,
    ):
        await saq_tasks.scan_task({}, plan_run_id=42, is_final=True)

    poll_calls = [c for c in to_thread.await_args_list if c.args[0] is scan_sync]
    assert len(poll_calls) == 1


@pytest.mark.asyncio
async def test_scan_task_no_hosts_triggered_skips_poll(monkeypatch):
    """scan_task skips poll loop when no ONLINE hosts found."""
    from backend.tasks import saq_tasks

    to_thread = AsyncMock(return_value="1")
    monkeypatch.setattr(saq_tasks, "asyncio_sleep", AsyncMock())
    monkeypatch.setattr(saq_tasks, "asyncio_to_thread", to_thread)

    with patch("backend.core.database.SessionLocal", return_value=_host_rows_db([("host-1", "OFFLINE")])), \
         patch("backend.realtime.socketio_server.emit_agent_control", new=AsyncMock()):
        mock_queue = MagicMock()
        mock_queue.enqueue = AsyncMock()
        with patch("backend.tasks.saq_worker.get_queue", return_value=mock_queue), \
             patch("saq.Job", MagicMock()):
            await saq_tasks.scan_task({}, plan_run_id=42, is_final=True)

    to_thread.assert_not_awaited()


@pytest.mark.asyncio
async def test_scan_task_records_zero_artifacts_after_poll_exhausted(monkeypatch, caplog):
    """A fleet-wide agent scan failure must not end as a silent SUCCESS."""
    from backend.tasks import saq_tasks

    scan_sync, hosts_done, record_archive = MagicMock(), MagicMock(), MagicMock()

    async def fake_to_thread(fn, *a, **kw):
        if fn is scan_sync:
            return ""
        if fn is hosts_done:
            return 0
        return fn(*a, **kw)

    queue = MagicMock()
    queue.enqueue = AsyncMock()
    with caplog.at_level("ERROR"), _scan_task_env(
        saq_tasks, monkeypatch, _host_rows_db([("host-1", "ONLINE")]),
        to_thread=AsyncMock(side_effect=fake_to_thread), scan_sync=scan_sync,
        hosts_done=hosts_done, record_archive=record_archive, queue=queue,
    ):
        await saq_tasks.scan_task({}, plan_run_id=42, is_final=True)

    assert "saq_scan_no_artifacts plan_run=42" in caplog.text
    record_archive.assert_called_once_with(
        42, hosts_triggered=1, artifacts_registered=0, hosts_with_artifacts=0
    )


@pytest.mark.asyncio
async def test_scan_task_counts_final_registration_attempt(monkeypatch):
    """The post-poll retry registers artifacts, so it must update the counts."""
    from backend.tasks import saq_tasks

    scan_sync, hosts_done, record_archive = MagicMock(), MagicMock(), MagicMock()
    scans = 0

    async def fake_to_thread(fn, *a, **kw):
        nonlocal scans
        if fn is scan_sync:
            scans += 1
            # Nothing during the poll window; the artifact lands just after it.
            return "" if scans <= 30 else "1"
        if fn is hosts_done:
            return 0 if scans <= 30 else 1
        return fn(*a, **kw)

    queue = MagicMock()
    queue.enqueue = AsyncMock()
    with _scan_task_env(
        saq_tasks, monkeypatch, _host_rows_db([("host-1", "ONLINE")]),
        to_thread=AsyncMock(side_effect=fake_to_thread), scan_sync=scan_sync,
        hosts_done=hosts_done, record_archive=record_archive, queue=queue,
    ):
        await saq_tasks.scan_task({}, plan_run_id=42, is_final=True)

    record_archive.assert_called_once_with(
        42, hosts_triggered=1, artifacts_registered=1, hosts_with_artifacts=1
    )


@pytest.mark.asyncio
async def test_scan_task_final_scan_runs_on_partial_coverage(monkeypatch):
    """A partially-covered run must still get the post-poll retry.

    Gating the retry on "nothing registered at all" stranded any ``_org.xls``
    that landed inside the last poll interval: never registered, never merged.
    """
    from backend.tasks import saq_tasks

    scan_sync, hosts_done, record_archive = MagicMock(), MagicMock(), MagicMock()
    scans = 0

    async def fake_to_thread(fn, *a, **kw):
        nonlocal scans
        if fn is scan_sync:
            scans += 1
            # host-1 lands immediately; host-2 only after the poll window closes.
            if scans == 1:
                return "1"
            return "" if scans <= 30 else "1"
        if fn is hosts_done:
            return 1 if scans <= 30 else 2
        return fn(*a, **kw)

    queue = MagicMock()
    queue.enqueue = AsyncMock()
    with _scan_task_env(
        saq_tasks, monkeypatch, _host_rows_db([("host-1", "ONLINE"), ("host-2", "ONLINE")]),
        to_thread=AsyncMock(side_effect=fake_to_thread), scan_sync=scan_sync,
        hosts_done=hosts_done, record_archive=record_archive, queue=queue,
    ):
        await saq_tasks.scan_task({}, plan_run_id=43, is_final=True)

    record_archive.assert_called_once_with(
        43, hosts_triggered=2, artifacts_registered=2, hosts_with_artifacts=2
    )


@pytest.mark.asyncio
async def test_scan_task_chains_on_partial_coverage_with_warning(monkeypatch, caplog):
    """Partial host coverage still merges, loudly.

    Withholding upload/merge here would turn "one slow or broken host" into
    "the whole run produces no report" — the exact outcome this path exists to
    prevent. The shortfall is a WARNING plus ``run_context.archive``, not a
    reason to strand the reports the other hosts did deliver.
    """
    from backend.tasks import saq_tasks

    scan_sync, hosts_done, record_archive = MagicMock(), MagicMock(), MagicMock()
    scans = 0

    async def fake_to_thread(fn, *a, **kw):
        nonlocal scans
        if fn is scan_sync:
            scans += 1
            return "2" if scans == 1 else ""
        if fn is hosts_done:
            return 1  # host-2 never delivers.
        return fn(*a, **kw)

    queue = MagicMock()
    queue.enqueue = AsyncMock()
    with caplog.at_level("WARNING"), _scan_task_env(
        saq_tasks, monkeypatch, _host_rows_db([("host-1", "ONLINE"), ("host-2", "ONLINE")]),
        to_thread=AsyncMock(side_effect=fake_to_thread), scan_sync=scan_sync,
        hosts_done=hosts_done, record_archive=record_archive, queue=queue,
    ) as job_cls:
        await saq_tasks.scan_task({}, plan_run_id=44, is_final=True)

    assert "saq_scan_partial_artifacts plan_run=44 hosts=1/2" in caplog.text
    assert "saq_scan_no_artifacts" not in caplog.text
    record_archive.assert_called_once_with(
        44, hosts_triggered=2, artifacts_registered=2, hosts_with_artifacts=1
    )
    functions = [c.kwargs["function"] for c in job_cls.call_args_list]
    assert functions == ["upload_task", "merge_task"]


# ---------------------------------------------------------------------------
# merge_task → extract_task chain
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_merge_task_enqueues_extract_on_success():
    """merge_task should wait for upload + devices then enqueue extract_task."""
    from backend.tasks import saq_tasks

    wait_upload = AsyncMock(return_value=True)
    wait_devices = AsyncMock(return_value=2)
    with patch("asyncio.to_thread", new=AsyncMock(return_value="ok")), \
         patch.object(saq_tasks, "_wait_for_upload_task", wait_upload), \
         patch.object(saq_tasks, "_wait_for_devices_on_nfs", wait_devices):
        mock_queue = MagicMock()
        mock_queue.enqueue = AsyncMock()
        with patch("backend.tasks.saq_worker.get_queue", return_value=mock_queue), \
             patch("saq.Job") as mock_job_cls:
            await saq_tasks.merge_task({}, plan_run_id=42)

    wait_upload.assert_awaited_once_with(42)
    wait_devices.assert_awaited_once_with(42)
    mock_job_cls.assert_called_once()
    assert mock_job_cls.call_args.kwargs["function"] == "extract_task"
    mock_queue.enqueue.assert_awaited_once()


@pytest.mark.asyncio
async def test_merge_task_skips_extract_when_merge_skipped():
    """merge_task should not wait or enqueue extract when merge skipped."""
    from backend.tasks import saq_tasks

    wait_upload = AsyncMock()
    wait_devices = AsyncMock()
    with patch("asyncio.to_thread", new=AsyncMock(return_value="")), \
         patch.object(saq_tasks, "_wait_for_upload_task", wait_upload), \
         patch.object(saq_tasks, "_wait_for_devices_on_nfs", wait_devices):
        mock_queue = MagicMock()
        mock_queue.enqueue = AsyncMock()
        with patch("backend.tasks.saq_worker.get_queue", return_value=mock_queue), \
             patch("saq.Job", MagicMock()):
            await saq_tasks.merge_task({}, plan_run_id=42)

    wait_upload.assert_not_awaited()
    wait_devices.assert_not_awaited()
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

    orig_interval = saq_tasks._UPLOAD_WAIT_INTERVAL
    orig_max = saq_tasks._UPLOAD_WAIT_MAX
    saq_tasks._UPLOAD_WAIT_INTERVAL = 1
    saq_tasks._UPLOAD_WAIT_MAX = 2
    saq_tasks.asyncio_sleep = AsyncMock()
    saq_tasks.asyncio_to_thread = AsyncMock(return_value={"status": "active"})
    try:
        result = await saq_tasks._wait_for_upload_task(42)
    finally:
        saq_tasks._UPLOAD_WAIT_INTERVAL = orig_interval
        saq_tasks._UPLOAD_WAIT_MAX = orig_max

    assert result is False
    assert saq_tasks.asyncio_to_thread.await_count >= 1


@pytest.mark.asyncio
async def test_wait_for_devices_on_nfs_polls_until_dirs_appear():
    """_wait_for_devices_on_nfs returns dir count once NFS has event directories."""
    from backend.tasks import saq_tasks

    counts = [0, 2]
    idx = 0

    def fake_count(_plan_run_id: int) -> int:
        nonlocal idx
        n = counts[min(idx, len(counts) - 1)]
        idx += 1
        return n

    saq_tasks.asyncio_sleep = AsyncMock()
    saq_tasks.asyncio_to_thread = AsyncMock(
        side_effect=lambda fn, *a: fake_count(a[0]),
    )

    result = await saq_tasks._wait_for_devices_on_nfs(42)

    assert result == 2
    assert saq_tasks.asyncio_to_thread.await_count == 2
    saq_tasks.asyncio_sleep.assert_awaited_once()


def test_count_devices_event_dirs_matches_timestamp_prefix(tmp_path, monkeypatch):
    """_count_devices_event_dirs_sync only counts YYYY-MM-DD_* style dirs."""
    from backend.tasks import saq_tasks

    monkeypatch.setenv("STP_AEE_NFS_ROOT", str(tmp_path))
    devices = tmp_path / "devices" / "99"
    devices.mkdir(parents=True)
    (devices / "2026-06-25_14-30-00_db.01").mkdir()
    (devices / "test_dir").mkdir()

    assert saq_tasks._count_devices_event_dirs_sync(99) == 1


def test_scan_task_merge_job_timeout_covers_poll_budget():
    """merge_task SAQ timeout must cover merge subprocess + upload/devices polls."""
    from backend.tasks import saq_tasks

    assert saq_tasks._MERGE_TASK_SAQ_TIMEOUT >= (
        saq_tasks._MERGE_SYNC_TIMEOUT
        + saq_tasks._UPLOAD_WAIT_MAX
        + saq_tasks._DEVICES_POLL_MAX
    )


@pytest.mark.asyncio
async def test_scan_task_enqueues_upload_and_merge_only(monkeypatch):
    """scan_task should not enqueue extract_task (chained from merge_task)."""
    from backend.tasks import saq_tasks

    scan_sync, hosts_done, record_archive = MagicMock(), MagicMock(), MagicMock()

    async def fake_to_thread(fn, *a, **kw):
        if fn is scan_sync:
            return "2"
        if fn is hosts_done:
            return 1
        return fn(*a, **kw)

    queue = MagicMock()
    queue.enqueue = AsyncMock()
    with _scan_task_env(
        saq_tasks, monkeypatch, _host_rows_db([("host-1", "ONLINE")]),
        to_thread=AsyncMock(side_effect=fake_to_thread), scan_sync=scan_sync,
        hosts_done=hosts_done, record_archive=record_archive, queue=queue,
    ) as mock_job_cls:
        await saq_tasks.scan_task({}, plan_run_id=42, is_final=True)

    assert mock_job_cls.call_count == 2
    functions = [c.kwargs["function"] for c in mock_job_cls.call_args_list]
    assert functions == ["upload_task", "merge_task"]
    assert "extract_task" not in functions


# ---------------------------------------------------------------------------
# P1-3: auto_archive_sweep rate-limiting + incremental
# ---------------------------------------------------------------------------


def _mock_auto_archive_db(mock_db, *, plan, run, scan_count: int, last_scan_at=None):
    """Wire mock db.query for per-plan auto_archive_sweep."""
    plan_query = MagicMock()
    plan_query.filter.return_value = plan_query
    plan_query.all.return_value = [plan]

    run_query = MagicMock()
    run_query.filter.return_value = run_query
    run_query.order_by.return_value = run_query
    run_query.first.return_value = run

    def _query(model):
        name = getattr(model, "__name__", str(model))
        if name == "Plan":
            return plan_query
        if name == "PlanRun":
            return run_query
        return MagicMock()

    mock_db.query.side_effect = _query
    execute_result = MagicMock()
    if last_scan_at is None:
        execute_result.scalar_one.side_effect = [scan_count]
    else:
        execute_result.scalar_one.side_effect = [scan_count, last_scan_at]
    mock_db.execute.return_value = execute_result


def test_auto_archive_sweep_first_scan_is_final():
    """First sweep (no scan artifacts) enqueues with is_final=True."""
    import backend.scheduler.cron_scheduler as mod

    mock_db = MagicMock()
    session_cm = MagicMock()
    session_cm.__enter__ = MagicMock(return_value=mock_db)
    session_cm.__exit__ = MagicMock(return_value=False)
    mock_SessionLocal = MagicMock(return_value=session_cm)

    mock_plan = MagicMock()
    mock_plan.id = 10
    mock_plan.auto_archive_interval_seconds = 3600

    mock_run = MagicMock()
    mock_run.id = 1
    mock_run.status = "SUCCESS"
    mock_run.ended_at = datetime.now(timezone.utc) - timedelta(hours=2)

    _mock_auto_archive_db(mock_db, plan=mock_plan, run=mock_run, scan_count=0)

    orig = mod.SessionLocal
    mod.SessionLocal = mock_SessionLocal

    try:
        with patch("backend.services.dedup_scan.enqueue_dedup_terminal_sync") as mock_enqueue:
            mod.auto_archive_sweep()
            mock_enqueue.assert_called_once_with(1, is_final=True)
    finally:
        mod.SessionLocal = orig


def test_auto_archive_sweep_skips_failed_run_without_confirmation():
    import backend.scheduler.cron_scheduler as mod

    mock_db = MagicMock()
    session_cm = MagicMock()
    session_cm.__enter__ = MagicMock(return_value=mock_db)
    session_cm.__exit__ = MagicMock(return_value=False)
    mock_plan = MagicMock(id=10, auto_archive_interval_seconds=3600)
    mock_run = MagicMock(
        id=1,
        status="FAILED",
        ended_at=datetime.now(timezone.utc) - timedelta(hours=2),
    )
    _mock_auto_archive_db(mock_db, plan=mock_plan, run=mock_run, scan_count=0)

    orig = mod.SessionLocal
    mod.SessionLocal = MagicMock(return_value=session_cm)
    try:
        with patch(
            "backend.services.dedup_scan.enqueue_dedup_terminal_sync"
        ) as mock_enqueue:
            mod.auto_archive_sweep()
            mock_enqueue.assert_not_called()
    finally:
        mod.SessionLocal = orig


def test_auto_archive_sweep_skips_terminal_already_scanned():
    """Terminal run with scan artifacts is never scanned again."""
    import backend.scheduler.cron_scheduler as mod

    mock_db = MagicMock()
    session_cm = MagicMock()
    session_cm.__enter__ = MagicMock(return_value=mock_db)
    session_cm.__exit__ = MagicMock(return_value=False)
    mock_SessionLocal = MagicMock(return_value=session_cm)

    mock_plan = MagicMock()
    mock_plan.id = 10
    mock_plan.auto_archive_interval_seconds = 3600

    mock_run = MagicMock()
    mock_run.id = 2
    mock_run.status = "SUCCESS"
    mock_run.ended_at = datetime.now(timezone.utc) - timedelta(hours=5)

    last_scan_time = datetime.now(timezone.utc) - timedelta(minutes=30)
    _mock_auto_archive_db(
        mock_db, plan=mock_plan, run=mock_run, scan_count=1, last_scan_at=last_scan_time,
    )

    orig = mod.SessionLocal
    mod.SessionLocal = mock_SessionLocal

    try:
        with patch("backend.services.dedup_scan.enqueue_dedup_terminal_sync") as mock_enqueue:
            mod.auto_archive_sweep()
            mock_enqueue.assert_not_called()
    finally:
        mod.SessionLocal = orig


def test_auto_archive_sweep_running_incremental_enqueues_after_interval():
    """RUNNING PlanRun gets incremental scan when interval elapsed since last scan."""
    import backend.scheduler.cron_scheduler as mod

    mock_db = MagicMock()
    session_cm = MagicMock()
    session_cm.__enter__ = MagicMock(return_value=mock_db)
    session_cm.__exit__ = MagicMock(return_value=False)
    mock_SessionLocal = MagicMock(return_value=session_cm)

    mock_plan = MagicMock()
    mock_plan.id = 10
    mock_plan.auto_archive_interval_seconds = 3600

    mock_run = MagicMock()
    mock_run.id = 3
    mock_run.status = "RUNNING"
    mock_run.ended_at = None

    last_scan_time = datetime.now(timezone.utc) - timedelta(hours=2)
    _mock_auto_archive_db(
        mock_db, plan=mock_plan, run=mock_run, scan_count=2, last_scan_at=last_scan_time,
    )

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

    mock_plan = MagicMock()
    mock_plan.id = 10
    mock_plan.auto_archive_interval_seconds = 3600

    mock_run = MagicMock()
    mock_run.id = 4
    mock_run.status = "FAILED"
    mock_run.ended_at = datetime.now(timezone.utc) - timedelta(minutes=30)

    _mock_auto_archive_db(mock_db, plan=mock_plan, run=mock_run, scan_count=0)

    orig = mod.SessionLocal
    mod.SessionLocal = mock_SessionLocal

    try:
        with patch("backend.services.dedup_scan.enqueue_dedup_terminal_sync") as mock_enqueue:
            mod.auto_archive_sweep()
            mock_enqueue.assert_not_called()
    finally:
        mod.SessionLocal = orig


def test_auto_archive_sweep_prefers_running_over_older_terminal():
    """Only the active RUNNING PlanRun is scanned, not older terminal runs."""
    import backend.scheduler.cron_scheduler as mod

    mock_db = MagicMock()
    session_cm = MagicMock()
    session_cm.__enter__ = MagicMock(return_value=mock_db)
    session_cm.__exit__ = MagicMock(return_value=False)
    mock_SessionLocal = MagicMock(return_value=session_cm)

    mock_plan = MagicMock()
    mock_plan.id = 2
    mock_plan.auto_archive_interval_seconds = 3600

    mock_running = MagicMock()
    mock_running.id = 55
    mock_running.status = "RUNNING"
    mock_running.ended_at = None

    plan_query = MagicMock()
    plan_query.filter.return_value = plan_query
    plan_query.all.return_value = [mock_plan]

    running_query = MagicMock()
    running_query.filter.return_value = running_query
    running_query.order_by.return_value = running_query
    running_query.first.return_value = mock_running

    mock_db.query.side_effect = lambda model: (
        plan_query if getattr(model, "__name__", "") == "Plan" else running_query
    )

    execute_result = MagicMock()
    execute_result.scalar_one.side_effect = [0, None]
    mock_db.execute.return_value = execute_result

    orig = mod.SessionLocal
    mod.SessionLocal = mock_SessionLocal

    try:
        with patch("backend.services.dedup_scan.enqueue_dedup_terminal_sync") as mock_enqueue:
            mod.auto_archive_sweep()
            mock_enqueue.assert_called_once_with(55, is_final=False)
    finally:
        mod.SessionLocal = orig
