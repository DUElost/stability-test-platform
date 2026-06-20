# -*- coding: utf-8 -*-
"""Unit tests for cron_scheduler — overlap protection, schedule dedup, retention.

ADR-0020 收口后 ``cron_scheduler`` 重构为 module-level async/sync functions
（``_fire_schedule`` / ``check_and_fire_schedules`` / ``run_retention_cleanup``）。
本文件覆盖 ``_fire_schedule`` 在新去重 + 重叠跳过路径下的核心分支，以及
``run_retention_cleanup`` 的 PlanRun 保留期清理。
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _schedule_ts(value: datetime) -> datetime:
    return value.replace(tzinfo=None)


class FakeTaskSchedule:
    """Mimics TaskSchedule ORM row (ADR-0020：仅 plan_id 触发，无 params/target)."""

    def __init__(self, id=1, name="patrol", cron_expression="*/3 * * * *",
                 plan_id=42, enabled=True, next_run_at=None, device_ids=None):
        self.id = id
        self.name = name
        self.cron_expression = cron_expression
        self.plan_id = plan_id
        self.enabled = enabled
        self.next_run_at = next_run_at or datetime.now(timezone.utc)
        self.device_ids = device_ids or []
        self.last_run_at = None


def _make_db(*, dedup_hit: bool = False, active_runs: int = 0,
             dedup_raises: bool = False, overlap_raises: bool = False):
    """Build an AsyncMock db that returns dedup/overlap results in order."""
    db = MagicMock()
    db.get_bind.return_value = MagicMock(dialect=MagicMock(name="sqlite"))
    db.commit = AsyncMock()
    db.rollback = AsyncMock()

    def execute_factory():
        calls = {"i": 0}

        async def _execute(stmt, *args, **kwargs):
            calls["i"] += 1
            # 1st call: dedup query (_recently_triggered_by_schedule)
            if calls["i"] == 1:
                if dedup_raises:
                    raise RuntimeError("dedup probe failed")
                row = MagicMock()
                row.first.return_value = (1,) if dedup_hit else None
                return row
            # 2nd call: overlap RUNNING count
            if calls["i"] == 2:
                if overlap_raises:
                    raise RuntimeError("overlap probe failed")
                scalars = MagicMock()
                scalars.scalars.return_value.all.return_value = [object()] * active_runs
                return scalars
            # subsequent calls inside dispatcher are not under test here
            return MagicMock()

        return _execute

    db.execute = AsyncMock(side_effect=execute_factory())
    return db


# ===========================================================================
# _fire_schedule — async branches
# ===========================================================================

class TestFireSchedule:
    @pytest.mark.asyncio
    async def test_postgresql_dedup_query_binds_jsonb_containment_value(self):
        """PostgreSQL JSONB dedup query must bind schedule context for asyncpg."""
        from sqlalchemy.dialects.postgresql.asyncpg import PGDialect_asyncpg

        from backend.scheduler.cron_scheduler import _recently_triggered_by_schedule

        dialect = MagicMock()
        dialect.name = "postgresql"
        bind = MagicMock()
        bind.dialect = dialect
        db = MagicMock()
        db.get_bind.return_value = bind

        async def execute(stmt, *args, **kwargs):
            compiled = stmt.compile(dialect=PGDialect_asyncpg())
            sql = str(compiled)
            assert ":sc::jsonb" not in sql
            assert {"schedule_id": 66} in compiled.params.values()
            row = MagicMock()
            row.first.return_value = None
            return row

        db.execute = AsyncMock(side_effect=execute)

        result = await _recently_triggered_by_schedule(
            db, 66, datetime.now(timezone.utc),
        )

        assert result is False

    @pytest.mark.asyncio
    async def test_dedup_skips_dispatch(self):
        """Same schedule fired in dedup window → skip dispatch."""
        from backend.scheduler.cron_scheduler import _fire_schedule

        now = datetime.now(timezone.utc)
        sched = FakeTaskSchedule(plan_id=42, next_run_at=now - timedelta(seconds=5))
        db = _make_db(dedup_hit=True)

        with patch("backend.scheduler.cron_scheduler._compute_next_run") as mock_next, \
             patch("backend.scheduler.cron_scheduler._dispatch_plan_async",
                   new_callable=AsyncMock) as mock_dispatch:
            mock_next.return_value = now + timedelta(minutes=3)
            await _fire_schedule(db, sched, now)

        mock_dispatch.assert_not_called()
        assert sched.next_run_at == _schedule_ts(now + timedelta(minutes=3))
        assert sched.last_run_at is None

    @pytest.mark.asyncio
    async def test_overlap_skips_dispatch(self):
        """Active RUNNING PlanRun for same plan → skip dispatch."""
        from backend.scheduler.cron_scheduler import _fire_schedule

        now = datetime.now(timezone.utc)
        sched = FakeTaskSchedule(plan_id=42, next_run_at=now - timedelta(seconds=5))
        db = _make_db(dedup_hit=False, active_runs=1)

        with patch("backend.scheduler.cron_scheduler._compute_next_run") as mock_next, \
             patch("backend.scheduler.cron_scheduler._dispatch_plan_async",
                   new_callable=AsyncMock) as mock_dispatch:
            mock_next.return_value = now + timedelta(minutes=3)
            await _fire_schedule(db, sched, now)

        mock_dispatch.assert_not_called()
        assert sched.next_run_at == _schedule_ts(now + timedelta(minutes=3))
        assert sched.last_run_at is None

    @pytest.mark.asyncio
    async def test_dispatch_when_clear(self):
        """No dedup, no overlap → dispatch with schedule_id propagated."""
        from backend.scheduler.cron_scheduler import _fire_schedule

        now = datetime.now(timezone.utc)
        sched = FakeTaskSchedule(
            id=7, plan_id=42, device_ids=[1, 2],
            next_run_at=now - timedelta(seconds=5),
        )
        db = _make_db(dedup_hit=False, active_runs=0)

        with patch("backend.scheduler.cron_scheduler._compute_next_run") as mock_next, \
             patch("backend.scheduler.cron_scheduler._dispatch_plan_async",
                   new_callable=AsyncMock) as mock_dispatch:
            mock_next.return_value = now + timedelta(minutes=3)
            await _fire_schedule(db, sched, now)

        mock_dispatch.assert_awaited_once_with(42, [1, 2], db, schedule_id=7)
        assert sched.last_run_at == _schedule_ts(now)
        assert sched.next_run_at == _schedule_ts(now + timedelta(minutes=3))

    @pytest.mark.asyncio
    async def test_dedup_failure_fails_open(self):
        """Dedup probe error → log and continue (fail-open)."""
        from backend.scheduler.cron_scheduler import _fire_schedule

        now = datetime.now(timezone.utc)
        sched = FakeTaskSchedule(plan_id=42, next_run_at=now - timedelta(seconds=5))
        # dedup raises, then overlap returns 0 active → dispatch should occur
        db = _make_db(dedup_raises=True, active_runs=0)

        with patch("backend.scheduler.cron_scheduler._compute_next_run") as mock_next, \
             patch("backend.scheduler.cron_scheduler._dispatch_plan_async",
                   new_callable=AsyncMock) as mock_dispatch:
            mock_next.return_value = now + timedelta(minutes=3)
            await _fire_schedule(db, sched, now)

        mock_dispatch.assert_awaited_once()
        db.rollback.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_overlap_failure_fails_closed(self):
        """Overlap probe error → skip dispatch (fail-closed) per concurrency safety."""
        from backend.scheduler.cron_scheduler import _fire_schedule

        now = datetime.now(timezone.utc)
        sched = FakeTaskSchedule(plan_id=42, next_run_at=now - timedelta(seconds=5))
        db = _make_db(dedup_hit=False, overlap_raises=True)

        with patch("backend.scheduler.cron_scheduler._compute_next_run") as mock_next, \
             patch("backend.scheduler.cron_scheduler._dispatch_plan_async",
                   new_callable=AsyncMock) as mock_dispatch:
            mock_next.return_value = now + timedelta(minutes=3)
            await _fire_schedule(db, sched, now)

        mock_dispatch.assert_not_called()
        db.rollback.assert_awaited_once()
        assert sched.next_run_at == _schedule_ts(now + timedelta(minutes=3))
        assert sched.last_run_at is None


# ===========================================================================
# TaskSchedule timestamp values — PostgreSQL TIMESTAMP WITHOUT TIME ZONE
# ===========================================================================

class TestTaskScheduleTimestamps:
    def test_scheduler_next_run_is_naive_utc(self):
        from backend.scheduler.cron_scheduler import _compute_next_run

        result = _compute_next_run(
            "0 2 * * *",
            datetime(2026, 5, 8, 12, 41, tzinfo=timezone.utc),
        )

        assert result.tzinfo is None

    def test_schedule_api_next_run_is_naive_utc(self):
        from backend.api.routes.schedules import _compute_next_run

        result = _compute_next_run(
            "0 2 * * *",
            datetime(2026, 5, 8, 12, 41, tzinfo=timezone.utc),
        )

        assert result.tzinfo is None

    def test_task_schedule_created_at_default_is_naive_utc(self):
        from backend.models.schedule import TaskSchedule

        default_factory = TaskSchedule.__table__.c.created_at.default.arg
        result = default_factory(None)

        assert result.tzinfo is None


# ===========================================================================
# run_retention_cleanup — sync branch
# ===========================================================================

class FakeQuery:
    def __init__(self, items=None):
        self._items = items or []

    def filter(self, *args, **kwargs):
        return self

    def limit(self, n):
        return self

    def all(self):
        return self._items

    def delete(self, synchronize_session=False):
        return len(self._items)


class TestRunRetentionCleanup:
    def _patched_session(self, db_mock):
        """Patch SessionLocal context manager to yield ``db_mock``."""
        cm = MagicMock()
        cm.__enter__ = MagicMock(return_value=db_mock)
        cm.__exit__ = MagicMock(return_value=False)
        return cm

    def test_deletes_stale_runs(self):
        from backend.scheduler.cron_scheduler import run_retention_cleanup

        old_run = MagicMock(id=99, status="SUCCESS")
        db = MagicMock()
        db.query.return_value = FakeQuery(items=[old_run])

        with patch("backend.scheduler.cron_scheduler.SessionLocal",
                   return_value=self._patched_session(db)):
            run_retention_cleanup()

        db.commit.assert_called_once()

    def test_deletes_job_artifacts_before_job_instances(self):
        from backend.models.device_lease import DeviceLease
        from backend.models.job import JobArtifact, JobInstance, StepTrace
        from backend.models.plan_run import PlanRun
        from backend.models.resource_pool import ResourceAllocation
        from backend.scheduler.cron_scheduler import run_retention_cleanup

        old_run = MagicMock(id=99, status="SUCCESS")
        db = MagicMock()

        queries = {
            PlanRun: FakeQuery(items=[old_run]),
            StepTrace: FakeQuery(),
            DeviceLease: FakeQuery(),
            ResourceAllocation: FakeQuery(),
            JobArtifact: FakeQuery(),
            JobInstance: FakeQuery(),
        }

        def query_side_effect(model):
            return queries[model]

        db.query.side_effect = query_side_effect

        with patch("backend.scheduler.cron_scheduler.SessionLocal",
                   return_value=self._patched_session(db)):
            run_retention_cleanup()

        queried_models = [call.args[0] for call in db.query.call_args_list]
        assert JobArtifact in queried_models
        assert queried_models.index(JobArtifact) < queried_models.index(JobInstance)

    def test_no_stale_runs_no_commit(self):
        from backend.scheduler.cron_scheduler import run_retention_cleanup

        db = MagicMock()
        db.query.return_value = FakeQuery(items=[])

        with patch("backend.scheduler.cron_scheduler.SessionLocal",
                   return_value=self._patched_session(db)):
            run_retention_cleanup()

        db.commit.assert_not_called()

    def test_failure_rolls_back(self):
        from backend.scheduler.cron_scheduler import run_retention_cleanup

        db = MagicMock()
        db.query.side_effect = RuntimeError("simulated")

        with patch("backend.scheduler.cron_scheduler.SessionLocal",
                   return_value=self._patched_session(db)):
            run_retention_cleanup()

        db.rollback.assert_called_once()
