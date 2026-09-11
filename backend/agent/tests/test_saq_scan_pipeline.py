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
    with patch("backend.realtime.socketio_server.call_agent_control", new=AsyncMock(return_value=True)), \
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


def test_scan_poll_budget_scales_with_hosts(monkeypatch):
    """#732: STP_SCAN_POLL_MAX_WAIT + n * PER_HOST."""
    from backend.tasks import saq_tasks

    monkeypatch.setenv("STP_SCAN_POLL_MAX_WAIT", "300")
    monkeypatch.setenv("STP_SCAN_POLL_PER_HOST_SECONDS", "2")
    assert saq_tasks._scan_poll_max_wait_seconds(50) == 400


def test_scan_poll_grace_when_near_complete(monkeypatch):
    from backend.tasks import saq_tasks

    monkeypatch.setenv("STP_SCAN_POLL_GRACE_SECONDS", "120")
    monkeypatch.setenv("STP_SCAN_POLL_GRACE_RATIO", "0.9")
    monkeypatch.setenv("STP_SCAN_POLL_GRACE_MAX_MISSING", "3")
    assert saq_tasks._scan_poll_grace_seconds(49, 50) == 120
    assert saq_tasks._scan_poll_grace_seconds(40, 50) == 0
    assert saq_tasks._scan_poll_grace_seconds(50, 50) == 0


@pytest.mark.asyncio
async def test_scan_task_applies_grace_for_near_complete_fleet(monkeypatch, caplog):
    """#732: at primary deadline with ≥90% ready, poll continues into grace."""
    from backend.tasks import saq_tasks

    monkeypatch.setenv("STP_SCAN_POLL_INTERVAL", "10")
    monkeypatch.setenv("STP_SCAN_POLL_MAX_WAIT", "30")
    monkeypatch.setenv("STP_SCAN_POLL_PER_HOST_SECONDS", "0")
    monkeypatch.setenv("STP_SCAN_POLL_GRACE_SECONDS", "20")
    monkeypatch.setenv("STP_SCAN_POLL_GRACE_RATIO", "0.9")
    monkeypatch.setenv("STP_SCAN_POLL_GRACE_MAX_MISSING", "2")

    scan_sync, hosts_done, record_archive = MagicMock(), MagicMock(), MagicMock()
    polls = 0
    # 10 hosts: after each poll return 9 until poll count >= 4, then 10
    host_rows = [(f"host-{i}", "ONLINE") for i in range(10)]

    async def fake_to_thread(fn, *a, **kw):
        nonlocal polls
        if fn is scan_sync:
            polls += 1
            return "1"
        if fn is hosts_done:
            return 10 if polls >= 4 else 9
        return fn(*a, **kw)

    queue = MagicMock()
    queue.enqueue = AsyncMock()
    with caplog.at_level("INFO"), _scan_task_env(
        saq_tasks, monkeypatch, host_rows,
        to_thread=AsyncMock(side_effect=fake_to_thread), scan_sync=scan_sync,
        hosts_done=hosts_done, record_archive=record_archive, queue=queue,
    ):
        await saq_tasks.scan_task({}, plan_run_id=42, is_final=True)

    assert "saq_scan_poll_grace" in caplog.text
    assert polls >= 4
    assert record_archive.call_args.kwargs["hosts_with_artifacts"] == 10


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
        45, hosts_triggered=1, artifacts_registered=2, hosts_with_artifacts=1,
        hosts_not_acked=0,
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
        46, hosts_triggered=1, artifacts_registered=2, hosts_with_artifacts=1,
        hosts_not_acked=0,
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

    with patch("backend.realtime.socketio_server.call_agent_control", new=AsyncMock(return_value=True)):
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
        42, hosts_triggered=1, artifacts_registered=0, hosts_with_artifacts=0,
        hosts_not_acked=0,
    )


@pytest.mark.asyncio
async def test_scan_task_records_no_ack_hosts(monkeypatch, caplog):
    """Agent 未回执 scan_now 的 host 记入 archive，不再静默丢失。"""
    from backend.realtime import socketio_server
    from backend.tasks import saq_tasks

    scan_sync, hosts_done, record_archive = MagicMock(), MagicMock(), MagicMock()
    scan_calls = 0

    async def fake_to_thread(fn, *a, **kw):
        nonlocal scan_calls
        if fn is scan_sync:
            scan_calls += 1
            return "1" if scan_calls == 1 else ""
        if fn is hosts_done:
            return 1
        return fn(*a, **kw)

    queue = MagicMock()
    queue.enqueue = AsyncMock()
    with caplog.at_level("WARNING"), _scan_task_env(
        saq_tasks, monkeypatch, [("host-1", "ONLINE"), ("host-2", "ONLINE")],
        to_thread=AsyncMock(side_effect=fake_to_thread), scan_sync=scan_sync,
        hosts_done=hosts_done, record_archive=record_archive, queue=queue,
    ):
        # 覆盖 fixture 的 ack mock：只有 host-2 回执
        socketio_server.call_agent_control.side_effect = (
            lambda host_id, command, **kw: host_id == "host-2"
        )
        await saq_tasks.scan_task({}, plan_run_id=49, is_final=True)

    assert "saq_scan_emit_no_ack plan_run=49 hosts=host-1" in caplog.text
    record_archive.assert_called_once_with(
        49, hosts_triggered=2, artifacts_registered=1, hosts_with_artifacts=1,
        hosts_not_acked=1,
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
        42, hosts_triggered=1, artifacts_registered=1, hosts_with_artifacts=1,
        hosts_not_acked=0,
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
        43, hosts_triggered=2, artifacts_registered=2, hosts_with_artifacts=2,
        hosts_not_acked=0,
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
        44, hosts_triggered=2, artifacts_registered=2, hosts_with_artifacts=1,
        hosts_not_acked=0,
    )
    functions = [c.kwargs["function"] for c in job_cls.call_args_list]
    assert functions == ["upload_task", "merge_task"]


# ---------------------------------------------------------------------------
# merge_task → extract_task chain
# ---------------------------------------------------------------------------


def test_scan_task_merge_job_timeout_covers_poll_budget():
    """merge_task SAQ timeout must cover the dual-platform chain (#1085).

    预算公式：平台数 × 300（各平台 merge 工具 subprocess）+ 180（标记水位线）
    + 660（DLE pending）+ 120（文件 I/O 与调度余量）——旧公式只按单平台计，
    双平台链（2×300 + 180 + 660 = 1440s）会超出旧预算 1080s 被 SAQ 误杀。
    """
    from backend.core.dedup_platform import DEDUP_PLATFORMS
    from backend.tasks import saq_tasks

    assert saq_tasks._MERGE_PLATFORM_COUNT == len(DEDUP_PLATFORMS) == 2
    assert saq_tasks._MERGE_TASK_SAQ_TIMEOUT == (
        len(DEDUP_PLATFORMS) * saq_tasks._MERGE_TOOL_TIMEOUT_PER_PLATFORM
        + saq_tasks._UPLOAD_MARK_WAIT_MAX
        + saq_tasks._UPLOAD_WAIT_MAX
        + 120
    )
    assert saq_tasks._MERGE_TASK_SAQ_TIMEOUT == 1560


@pytest.mark.asyncio
async def test_scan_task_enqueues_upload_then_merge(monkeypatch):
    """scan_task enqueues upload_task then merge_task (extract chained from merge)."""
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

    assert mock_job_cls.call_count == 2
    functions = [c.kwargs["function"] for c in mock_job_cls.call_args_list]
    assert functions == ["upload_task", "merge_task"]
    assert "extract_task" not in functions
    keys = [c.kwargs["key"] for c in mock_job_cls.call_args_list]
    assert keys[0].startswith("upload:42:")
    assert keys[1].startswith("merge:42:")
    assert keys[0] != "upload:42"
    assert keys[1] != "merge:42"


@pytest.mark.asyncio
async def test_scan_task_enqueues_distinct_keys_per_round(monkeypatch):
    """#1111: incremental vs final must not share upload/merge SAQ keys."""
    from backend.tasks import saq_tasks

    scan_sync, hosts_done, record_archive = MagicMock(), MagicMock(), MagicMock()
    keys_by_run: list[list[str]] = []
    round_times = iter([
        datetime(2026, 9, 8, 10, 0, 0, tzinfo=timezone.utc),
        datetime(2026, 9, 8, 12, 0, 0, tzinfo=timezone.utc),
    ])

    class _FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return next(round_times)

    monkeypatch.setattr(saq_tasks, "datetime", _FixedDateTime)

    async def fake_to_thread(fn, *a, **kw):
        if fn is scan_sync:
            return "1"
        if fn is hosts_done:
            return 1
        return fn(*a, **kw)

    queue = MagicMock()
    queue.enqueue = AsyncMock(return_value=object())

    for is_final in (False, True):
        with _scan_task_env(
            saq_tasks, monkeypatch, [("host-1", "ONLINE")],
            to_thread=AsyncMock(side_effect=fake_to_thread), scan_sync=scan_sync,
            hosts_done=hosts_done, record_archive=record_archive, queue=queue,
        ) as mock_job_cls:
            await saq_tasks.scan_task({}, plan_run_id=7, is_final=is_final)
            keys_by_run.append([c.kwargs["key"] for c in mock_job_cls.call_args_list])

    inc_keys, final_keys = keys_by_run
    assert inc_keys[0].startswith("upload:7:")
    assert final_keys[0].startswith("upload:7:")
    assert inc_keys != final_keys, "rounds must not reuse upload/merge keys"


@pytest.mark.asyncio
async def test_enqueue_or_raise_on_dedup_none():
    """#1111: SAQ key collision (enqueue→None) must be observable / fail."""
    from backend.tasks import saq_tasks
    from saq import Job as SaqJob

    queue = MagicMock()
    queue.enqueue = AsyncMock(return_value=None)
    job = SaqJob(function="merge_task", kwargs={"plan_run_id": 1}, key="merge:1:x")
    with pytest.raises(RuntimeError, match="saq enqueue deduped"):
        await saq_tasks._enqueue_or_raise(
            queue, job, plan_run_id=1, what="merge_task",
        )


@pytest.mark.asyncio
async def test_scan_task_enqueues_upload_and_merge_logs(monkeypatch, caplog):
    """#213 Track A: scan_task logs the upload+merge follow-up chain."""
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
    assert functions == ["upload_task", "merge_task"]
    assert "saq_scan_enqueue_upload_and_merge plan_run=42" in caplog.text


@pytest.mark.asyncio
async def test_merge_task_waits_on_device_log_events(monkeypatch):
    """merge_task waits on DLE REMOTE/ARCHIVED before extract."""
    from backend.tasks import saq_tasks

    wait_remote = AsyncMock(return_value=True)
    count_remote = AsyncMock(return_value=3)

    async def fake_to_thread(fn, *a, **kw):
        # #1123: _run_sync_exclusive 经模块别名 asyncio_to_thread；merge 短路，
        # 其余（汇总写盘）透传，避免 blanket "ok" 弄坏 upload_summary dict。
        if getattr(fn, "__name__", "") == "run_merge_all_platforms_sync":
            return "ok"
        return fn(*a, **kw)

    monkeypatch.setattr(saq_tasks, "asyncio_to_thread", fake_to_thread)
    with patch.object(saq_tasks, "_wait_for_upload_mark", AsyncMock(return_value=True)), \
         patch.object(saq_tasks, "_wait_for_remote_device_log_events", wait_remote), \
         patch.object(saq_tasks, "_count_remote_device_log_events", count_remote), \
         patch.object(saq_tasks, "_summarize_upload_sync", return_value={"total": 0}), \
         patch.object(saq_tasks, "_write_run_context_sync"):
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

    async def fake_to_thread(fn, *a, **kw):
        if getattr(fn, "__name__", "") == "run_merge_all_platforms_sync":
            return "ok"
        return fn(*a, **kw)

    monkeypatch.setattr(saq_tasks, "asyncio_to_thread", fake_to_thread)
    with patch.object(saq_tasks, "_wait_for_upload_mark", AsyncMock(return_value=True)), \
         patch.object(saq_tasks, "_wait_for_remote_device_log_events", wait_remote), \
         patch.object(saq_tasks, "_count_remote_device_log_events", count_remote), \
         patch.object(saq_tasks, "_summarize_upload_sync", return_value={"total": 0}), \
         patch.object(saq_tasks, "_write_run_context_sync"):
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

    async def fake_to_thread(fn, *a, **kw):
        if getattr(fn, "__name__", "") == "run_merge_all_platforms_sync":
            return ""
        return fn(*a, **kw)

    monkeypatch.setattr(saq_tasks, "asyncio_to_thread", fake_to_thread)
    with patch.object(saq_tasks, "_wait_for_remote_device_log_events", wait_remote), \
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


@pytest.mark.asyncio
async def test_scan_task_reraises_when_followup_enqueue_fails(monkeypatch):
    """#1110: follow-up enqueue failure must fail scan_task for SAQ retry."""
    from backend.tasks import saq_tasks

    scan_sync = MagicMock(return_value="1")
    hosts_done = MagicMock(return_value=1)
    record_archive = MagicMock()

    async def fake_to_thread(fn, *a, **kw):
        if fn is scan_sync:
            return "1"
        if fn is hosts_done:
            return 1
        return fn(*a, **kw)

    queue = MagicMock()
    queue.enqueue = AsyncMock(side_effect=RuntimeError("redis down"))
    with _scan_task_env(
        saq_tasks, monkeypatch, [("host-1", "ONLINE")],
        to_thread=AsyncMock(side_effect=fake_to_thread), scan_sync=scan_sync,
        hosts_done=hosts_done, record_archive=record_archive, queue=queue,
    ):
        with pytest.raises(RuntimeError, match="redis down"):
            await saq_tasks.scan_task({}, plan_run_id=42, is_final=True)


@pytest.mark.asyncio
async def test_merge_task_reraises_when_extract_enqueue_fails(monkeypatch):
    """#1110: extract enqueue failure must fail merge_task for SAQ retry."""
    from backend.tasks import saq_tasks

    async def fake_to_thread(fn, *a, **kw):
        if getattr(fn, "__name__", "") == "run_merge_all_platforms_sync":
            return "ok"
        return fn(*a, **kw)

    monkeypatch.setattr(saq_tasks, "asyncio_to_thread", fake_to_thread)
    with patch.object(saq_tasks, "_wait_for_upload_mark", AsyncMock(return_value=True)), \
         patch.object(saq_tasks, "_wait_for_remote_device_log_events", AsyncMock(return_value=True)), \
         patch.object(saq_tasks, "_count_remote_device_log_events", AsyncMock(return_value=1)), \
         patch.object(saq_tasks, "_summarize_upload_sync", return_value={"total": 0}), \
         patch.object(saq_tasks, "_write_run_context_sync"), \
         patch.object(
             saq_tasks,
             "_enqueue_extract_task",
             AsyncMock(side_effect=RuntimeError("enqueue failed")),
         ):
        with pytest.raises(RuntimeError, match="enqueue failed"):
            await saq_tasks.merge_task({}, plan_run_id=42)


# ---------------------------------------------------------------------------
# P1-3: auto_archive_sweep rate-limiting + incremental
# ---------------------------------------------------------------------------


def _mock_auto_archive_db(
    mock_db,
    *,
    plan,
    run,
    scan_count: int,
    last_scan_at=None,
    merge_count: int | None = None,
    extract_context: dict | None = None,
    terminal_candidates=None,
):
    """Wire mock db.query for per-plan auto_archive_sweep.

    RUNNING path uses ``.first()``; terminal path (#833) uses ``.all()`` on a
    second PlanRun query. Pass ``terminal_candidates`` to override the list
    returned by that ``.all()`` (default: ``[run]`` when run is not RUNNING).
    """
    plan_query = MagicMock()
    plan_query.filter.return_value = plan_query
    plan_query.all.return_value = [plan]

    is_running = getattr(run, "status", None) == "RUNNING"
    running_query = MagicMock()
    running_query.filter.return_value = running_query
    running_query.order_by.return_value = running_query
    running_query.first.return_value = run if is_running else None

    if terminal_candidates is None:
        terminal_candidates = [] if is_running else [run]
    terminal_query = MagicMock()
    terminal_query.filter.return_value = terminal_query
    terminal_query.order_by.return_value = terminal_query
    terminal_query.all.return_value = list(terminal_candidates)

    plan_run_calls = {"n": 0}

    def _query(model):
        name = getattr(model, "__name__", str(model))
        if name == "Plan":
            return plan_query
        if name == "PlanRun":
            plan_run_calls["n"] += 1
            return running_query if plan_run_calls["n"] == 1 else terminal_query
        return MagicMock()

    mock_db.query.side_effect = _query
    execute_result = MagicMock()
    if last_scan_at is not None:
        execute_result.scalar_one.side_effect = [scan_count, last_scan_at]
    elif merge_count is not None:
        execute_result.scalar_one.side_effect = [scan_count, merge_count]
    else:
        execute_result.scalar_one.side_effect = [scan_count]
    mock_db.execute.return_value = execute_result
    if extract_context is not None:
        run.run_context = {"extract": extract_context}
        mock_db.get.return_value = run


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
    # FAILED is outside _AUTO_FINAL_STATUSES; terminal query returns empty.
    mock_run = MagicMock(
        id=1,
        status="FAILED",
        ended_at=datetime.now(timezone.utc) - timedelta(hours=2),
    )
    _mock_auto_archive_db(
        mock_db, plan=mock_plan, run=mock_run, scan_count=0, terminal_candidates=[],
    )

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
    """Terminal run with scan+merge+extract artifacts is never scanned again."""
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

    _mock_auto_archive_db(
        mock_db,
        plan=mock_plan,
        run=mock_run,
        scan_count=1,
        merge_count=1,
        extract_context={"copied": 1},
    )

    orig = mod.SessionLocal
    mod.SessionLocal = mock_SessionLocal

    try:
        with patch("backend.services.dedup_scan.enqueue_dedup_terminal_sync") as mock_enqueue:
            mod.auto_archive_sweep()
            mock_enqueue.assert_not_called()
    finally:
        mod.SessionLocal = orig


def test_auto_archive_sweep_retries_terminal_when_scan_only():
    """Terminal run with scan but no merge/extract should re-trigger archive (#1110)."""
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

    _mock_auto_archive_db(
        mock_db, plan=mock_plan, run=mock_run, scan_count=1, merge_count=0,
    )

    orig = mod.SessionLocal
    mod.SessionLocal = mock_SessionLocal

    try:
        with patch("backend.services.dedup_scan.enqueue_dedup_terminal_sync") as mock_enqueue:
            mod.auto_archive_sweep()
            mock_enqueue.assert_called_once_with(2, is_final=True)
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
    """Due-cutoff query excludes not-yet-due terminal runs (empty candidates)."""
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
    mock_run.status = "SUCCESS"
    mock_run.ended_at = datetime.now(timezone.utc) - timedelta(minutes=30)

    _mock_auto_archive_db(
        mock_db, plan=mock_plan, run=mock_run, scan_count=0, terminal_candidates=[],
    )

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

    _mock_auto_archive_db(
        mock_db, plan=mock_plan, run=mock_running, scan_count=0,
    )

    orig = mod.SessionLocal
    mod.SessionLocal = mock_SessionLocal

    try:
        with patch("backend.services.dedup_scan.enqueue_dedup_terminal_sync") as mock_enqueue:
            mod.auto_archive_sweep()
            mock_enqueue.assert_called_once_with(55, is_final=False)
    finally:
        mod.SessionLocal = orig


def test_auto_archive_sweep_serves_oldest_due_terminal_not_newest():
    """#833: when a newer terminal run exists, still enqueue the older due run."""
    import backend.scheduler.cron_scheduler as mod

    mock_db = MagicMock()
    session_cm = MagicMock()
    session_cm.__enter__ = MagicMock(return_value=mock_db)
    session_cm.__exit__ = MagicMock(return_value=False)
    mock_SessionLocal = MagicMock(return_value=session_cm)

    mock_plan = MagicMock()
    mock_plan.id = 10
    mock_plan.auto_archive_interval_seconds = 3600

    older = MagicMock()
    older.id = 1
    older.status = "SUCCESS"
    older.ended_at = datetime.now(timezone.utc) - timedelta(hours=5)

    newer = MagicMock()
    newer.id = 2
    newer.status = "SUCCESS"
    newer.ended_at = datetime.now(timezone.utc) - timedelta(hours=3)
    newer.run_context = {"extract": {"copied": 1}}

    # Oldest-first candidate list; newer already archive-complete → skip to older.
    _mock_auto_archive_db(
        mock_db,
        plan=mock_plan,
        run=older,
        scan_count=0,
        terminal_candidates=[older, newer],
    )
    # First candidate (older): scan_count=0 → enqueue without merge check.
    # (If older were complete we'd need more scalar_one values; here scan_count=0.)

    orig = mod.SessionLocal
    mod.SessionLocal = mock_SessionLocal

    try:
        with patch("backend.services.dedup_scan.enqueue_dedup_terminal_sync") as mock_enqueue:
            mod.auto_archive_sweep()
            mock_enqueue.assert_called_once_with(1, is_final=True)
    finally:
        mod.SessionLocal = orig


def test_auto_archive_sweep_skips_complete_newer_serves_older_incomplete():
    """#833: skip archive-complete newest, serve older incomplete terminal."""
    import backend.scheduler.cron_scheduler as mod

    mock_db = MagicMock()
    session_cm = MagicMock()
    session_cm.__enter__ = MagicMock(return_value=mock_db)
    session_cm.__exit__ = MagicMock(return_value=False)
    mock_SessionLocal = MagicMock(return_value=session_cm)

    mock_plan = MagicMock(id=10, auto_archive_interval_seconds=3600)

    older = MagicMock(
        id=1,
        status="SUCCESS",
        ended_at=datetime.now(timezone.utc) - timedelta(hours=5),
        run_context={},
    )
    newer = MagicMock(
        id=2,
        status="SUCCESS",
        ended_at=datetime.now(timezone.utc) - timedelta(hours=2),
        run_context={"extract": {"copied": 1}},
    )

    plan_query = MagicMock()
    plan_query.filter.return_value = plan_query
    plan_query.all.return_value = [mock_plan]

    running_query = MagicMock()
    running_query.filter.return_value = running_query
    running_query.order_by.return_value = running_query
    running_query.first.return_value = None

    terminal_query = MagicMock()
    terminal_query.filter.return_value = terminal_query
    terminal_query.order_by.return_value = terminal_query
    # Oldest-first as production query orders.
    terminal_query.all.return_value = [older, newer]

    calls = {"n": 0}

    def _query(model):
        name = getattr(model, "__name__", "")
        if name == "Plan":
            return plan_query
        if name == "PlanRun":
            calls["n"] += 1
            return running_query if calls["n"] == 1 else terminal_query
        return MagicMock()

    mock_db.query.side_effect = _query

    # older: scan_count=0 → enqueue immediately (no merge check)
    # (Order in candidates is oldest-first, so older is first.)
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


def test_auto_archive_sweep_skips_complete_oldest_then_serves_next():
    """#833: if oldest is archive-complete, move to next due terminal."""
    import backend.scheduler.cron_scheduler as mod

    mock_db = MagicMock()
    session_cm = MagicMock()
    session_cm.__enter__ = MagicMock(return_value=mock_db)
    session_cm.__exit__ = MagicMock(return_value=False)
    mock_SessionLocal = MagicMock(return_value=session_cm)

    mock_plan = MagicMock(id=10, auto_archive_interval_seconds=3600)

    older = MagicMock(
        id=1,
        status="SUCCESS",
        ended_at=datetime.now(timezone.utc) - timedelta(hours=5),
        run_context={"extract": {"copied": 1}},
    )
    newer = MagicMock(
        id=2,
        status="SUCCESS",
        ended_at=datetime.now(timezone.utc) - timedelta(hours=3),
        run_context={},
    )

    plan_query = MagicMock()
    plan_query.filter.return_value = plan_query
    plan_query.all.return_value = [mock_plan]

    running_query = MagicMock()
    running_query.filter.return_value = running_query
    running_query.order_by.return_value = running_query
    running_query.first.return_value = None

    terminal_query = MagicMock()
    terminal_query.filter.return_value = terminal_query
    terminal_query.order_by.return_value = terminal_query
    terminal_query.all.return_value = [older, newer]

    calls = {"n": 0}

    def _query(model):
        name = getattr(model, "__name__", "")
        if name == "Plan":
            return plan_query
        if name == "PlanRun":
            calls["n"] += 1
            return running_query if calls["n"] == 1 else terminal_query
        return MagicMock()

    mock_db.query.side_effect = _query

    # older: scan_count=1, merge_count=1 → complete, skip
    # newer: scan_count=0 → enqueue
    execute_result = MagicMock()
    execute_result.scalar_one.side_effect = [1, 1, 0]
    mock_db.execute.return_value = execute_result
    mock_db.get.side_effect = lambda model, pk: older if pk == 1 else newer

    orig = mod.SessionLocal
    mod.SessionLocal = mock_SessionLocal
    try:
        with patch("backend.services.dedup_scan.enqueue_dedup_terminal_sync") as mock_enqueue:
            mod.auto_archive_sweep()
            mock_enqueue.assert_called_once_with(2, is_final=True)
    finally:
        mod.SessionLocal = orig
