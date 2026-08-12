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


def _query_hosts_from_rows(rows):
    """Stand-in for ``_query_hosts_for_scan`` → ``(triggered, skipped)``."""

    def _query(plan_run_id: int, is_final: bool = False):
        triggered = []
        skipped = []
        for host_id, status in rows:
            if status == "ONLINE":
                triggered.append((
                    host_id,
                    {
                        "plan_run_id": plan_run_id,
                        "is_final": is_final,
                        "device_serials": [],
                        "run_date_stamps": [],
                    },
                ))
            else:
                skipped.append(host_id)
        return triggered, skipped

    return _query


@contextlib.contextmanager
def _scan_task_env(
    saq_tasks,
    monkeypatch,
    host_rows,
    *,
    to_thread,
    scan_sync,
    hosts_done,
    record_archive,
    queue,
):
    """Patch stack shared by the scan_task poll tests.

    ``asyncio_sleep`` / ``asyncio_to_thread`` go through ``monkeypatch`` so the
    module-level attributes are restored after each test instead of leaking a
    fake sleep into every later test in the session.
    """
    monkeypatch.setattr(saq_tasks, "asyncio_sleep", AsyncMock())
    monkeypatch.setattr(saq_tasks, "asyncio_to_thread", to_thread)
    monkeypatch.setattr(
        saq_tasks, "_query_hosts_for_scan", _query_hosts_from_rows(host_rows),
    )
    with patch("backend.realtime.socketio_server.emit_agent_control", new=AsyncMock()), \
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
        saq_tasks, monkeypatch, [("host-1", "ONLINE"), ("host-2", "ONLINE")],
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
        saq_tasks, monkeypatch, [("host-1", "ONLINE"), ("host-2", "ONLINE")],
        to_thread=AsyncMock(side_effect=fake_to_thread), scan_sync=scan_sync,
        hosts_done=hosts_done, record_archive=record_archive, queue=queue,
    ):
        await saq_tasks.scan_task({}, plan_run_id=42, is_final=True)

    assert polls == 3
    assert record_archive.call_args.kwargs["hosts_with_artifacts"] == 2


@pytest.mark.asyncio
async def test_scan_task_ignores_stale_artifacts_of_untriggered_hosts(monkeypatch):
    """Coverage must be scoped to this round's triggered hosts.

    Incremental scans reuse the ``plan_run_id``, so a run-wide host count lets a
    previous round's artifacts fill the quota: here host-a already has artifacts
    but is offline, and only host-b was triggered. Counting run-wide would give
    1/1 and break on the first check before host-b uploaded anything.
    """
    from backend.tasks import saq_tasks

    scan_sync, hosts_done, record_archive = MagicMock(), MagicMock(), MagicMock()
    polls = 0
    registered_hosts = {"host-a"}  # stale, from an earlier scan of the same run

    async def fake_to_thread(fn, *a, **kw):
        nonlocal polls
        if fn is scan_sync:
            polls += 1
            if polls == 2:
                registered_hosts.add("host-b")
                return "2"
            return ""
        if fn is hosts_done:
            # Mirrors the real query: intersect with the host_ids it was given,
            # so forgetting to pass ``triggered`` fails this test.
            _run_id, host_ids = a
            return len(registered_hosts & set(host_ids))
        return fn(*a, **kw)

    queue = MagicMock()
    queue.enqueue = AsyncMock()
    with _scan_task_env(
        saq_tasks, monkeypatch, [("host-a", "OFFLINE"), ("host-b", "ONLINE")],
        to_thread=AsyncMock(side_effect=fake_to_thread), scan_sync=scan_sync,
        hosts_done=hosts_done, record_archive=record_archive, queue=queue,
    ):
        await saq_tasks.scan_task({}, plan_run_id=45, is_final=True)

    # Kept polling past the first check instead of accepting host-a's leftovers.
    assert polls == 2
    record_archive.assert_called_once_with(
        45, hosts_triggered=1, artifacts_registered=2, hosts_with_artifacts=1
    )


@pytest.mark.asyncio
async def test_scan_task_ignores_same_hosts_previous_round_artifacts(monkeypatch):
    """A host's earlier-round artifacts must not satisfy this round.

    Incremental scans reuse the ``plan_run_id``, so host-a can already have
    artifacts from a previous round and be triggered again. Counting without the
    ``since`` watermark makes the very first check read 1/1 and break before
    host-a's new upload lands — merging last round's stale report.
    """
    from backend.tasks import saq_tasks

    scan_sync, hosts_done, record_archive = MagicMock(), MagicMock(), MagicMock()
    polls = 0
    # (host_id, created_at) rows already in plan_run_artifact for this run.
    stale = datetime(2026, 8, 8, 6, 0, tzinfo=timezone.utc)
    rows = [("host-a", stale)]

    async def fake_to_thread(fn, *a, **kw):
        nonlocal polls
        if fn is scan_sync:
            polls += 1
            if polls == 2:
                rows.append(("host-a", datetime.now(timezone.utc)))
                return "2"
            return ""
        if fn is hosts_done:
            _run_id, host_ids = a
            since = kw["since"]
            return len({
                h for h, created in rows if h in set(host_ids) and created >= since
            })
        return fn(*a, **kw)

    queue = MagicMock()
    queue.enqueue = AsyncMock()
    with _scan_task_env(
        saq_tasks, monkeypatch, [("host-a", "ONLINE")],
        to_thread=AsyncMock(side_effect=fake_to_thread), scan_sync=scan_sync,
        hosts_done=hosts_done, record_archive=record_archive, queue=queue,
    ):
        await saq_tasks.scan_task({}, plan_run_id=46, is_final=True)

    # Did not accept host-a's stale row on the first check.
    assert polls == 2
    record_archive.assert_called_once_with(
        46, hosts_triggered=1, artifacts_registered=2, hosts_with_artifacts=1
    )


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
        saq_tasks, monkeypatch, [("host-1", "ONLINE"), ("host-2", "ONLINE")],
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
    monkeypatch.setattr(
        saq_tasks, "_query_hosts_for_scan",
        lambda _plan_run_id, is_final=False: ([], ["host-1"]),
    )

    with patch("backend.realtime.socketio_server.emit_agent_control", new=AsyncMock()):
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
        saq_tasks, monkeypatch, [("host-1", "ONLINE")],
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
        saq_tasks, monkeypatch, [("host-1", "ONLINE")],
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
        saq_tasks, monkeypatch, [("host-1", "ONLINE"), ("host-2", "ONLINE")],
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
        saq_tasks, monkeypatch, [("host-1", "ONLINE"), ("host-2", "ONLINE")],
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
    assert functions == ["merge_task"]


# ---------------------------------------------------------------------------
# merge_task → extract_task chain
# ---------------------------------------------------------------------------


def test_scan_task_merge_job_timeout_covers_poll_budget():
    """merge_task SAQ timeout must cover merge subprocess + DLE wait."""
    from backend.tasks import saq_tasks

    assert saq_tasks._MERGE_TASK_SAQ_TIMEOUT >= (
        saq_tasks._MERGE_SYNC_TIMEOUT + saq_tasks._UPLOAD_WAIT_MAX + 120
    )


@pytest.mark.asyncio
async def test_scan_task_enqueues_merge_only(monkeypatch):
    """scan_task should enqueue merge_task only (extract chained from merge)."""
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
        saq_tasks, monkeypatch, [("host-1", "ONLINE")],
        to_thread=AsyncMock(side_effect=fake_to_thread), scan_sync=scan_sync,
        hosts_done=hosts_done, record_archive=record_archive, queue=queue,
    ) as mock_job_cls:
        await saq_tasks.scan_task({}, plan_run_id=42, is_final=True)

    assert mock_job_cls.call_count == 1
    functions = [c.kwargs["function"] for c in mock_job_cls.call_args_list]
    assert functions == ["merge_task"]
    assert "extract_task" not in functions


@pytest.mark.asyncio
async def test_scan_task_enqueues_merge_only_logs(monkeypatch, caplog):
    """#213 Track A: scan_task always enqueues merge only."""
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
    with caplog.at_level("INFO"), _scan_task_env(
        saq_tasks, monkeypatch, [("host-1", "ONLINE")],
        to_thread=AsyncMock(side_effect=fake_to_thread), scan_sync=scan_sync,
        hosts_done=hosts_done, record_archive=record_archive, queue=queue,
    ) as mock_job_cls:
        await saq_tasks.scan_task({}, plan_run_id=42, is_final=True)

    functions = [c.kwargs["function"] for c in mock_job_cls.call_args_list]
    assert functions == ["merge_task"]
    assert "saq_scan_enqueue_merge plan_run=42" in caplog.text


@pytest.mark.asyncio
async def test_merge_task_waits_on_device_log_events(monkeypatch):
    """merge_task waits on DLE REMOTE/ARCHIVED before extract."""
    from backend.tasks import saq_tasks

    wait_remote = AsyncMock(return_value=True)
    count_remote = AsyncMock(return_value=3)
    with patch("asyncio.to_thread", new=AsyncMock(return_value="ok")), \
         patch.object(saq_tasks, "_wait_for_remote_device_log_events", wait_remote), \
         patch.object(saq_tasks, "_count_remote_device_log_events", count_remote):
        mock_queue = MagicMock()
        mock_queue.enqueue = AsyncMock()
        with patch("backend.tasks.saq_worker.get_queue", return_value=mock_queue), \
             patch("saq.Job") as mock_job_cls:
            await saq_tasks.merge_task({}, plan_run_id=42)

    wait_remote.assert_awaited_once_with(42)
    count_remote.assert_awaited_once_with(42)
    assert mock_job_cls.call_args.kwargs["function"] == "extract_task"


@pytest.mark.asyncio
async def test_merge_task_enqueues_extract_on_success(monkeypatch):
    """merge_task should wait for DLE then enqueue extract_task."""
    from backend.tasks import saq_tasks

    wait_remote = AsyncMock(return_value=True)
    count_remote = AsyncMock(return_value=2)
    with patch("asyncio.to_thread", new=AsyncMock(return_value="ok")), \
         patch.object(saq_tasks, "_wait_for_remote_device_log_events", wait_remote), \
         patch.object(saq_tasks, "_count_remote_device_log_events", count_remote):
        mock_queue = MagicMock()
        mock_queue.enqueue = AsyncMock()
        with patch("backend.tasks.saq_worker.get_queue", return_value=mock_queue), \
             patch("saq.Job") as mock_job_cls:
            await saq_tasks.merge_task({}, plan_run_id=42)

    wait_remote.assert_awaited_once_with(42)
    count_remote.assert_awaited_once_with(42)
    mock_job_cls.assert_called_once()
    assert mock_job_cls.call_args.kwargs["function"] == "extract_task"
    mock_queue.enqueue.assert_awaited_once()


@pytest.mark.asyncio
async def test_merge_task_skips_extract_when_merge_skipped(monkeypatch):
    """merge_task should not wait or enqueue extract when merge skipped."""
    from backend.tasks import saq_tasks

    wait_remote = AsyncMock()
    count_remote = AsyncMock()
    with patch("asyncio.to_thread", new=AsyncMock(return_value="")), \
         patch.object(saq_tasks, "_wait_for_remote_device_log_events", wait_remote), \
         patch.object(saq_tasks, "_count_remote_device_log_events", count_remote):
        mock_queue = MagicMock()
        mock_queue.enqueue = AsyncMock()
        with patch("backend.tasks.saq_worker.get_queue", return_value=mock_queue), \
             patch("saq.Job") as mock_job_cls:
            await saq_tasks.merge_task({}, plan_run_id=42)

    wait_remote.assert_not_awaited()
    count_remote.assert_not_awaited()
    mock_job_cls.assert_not_called()
    mock_queue.enqueue.assert_not_awaited()


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
