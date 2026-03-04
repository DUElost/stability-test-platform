# -*- coding: utf-8 -*-
"""Unit tests for CronScheduler overlap protection and run cleanup."""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers — lightweight fakes that avoid real DB / asyncio
# ---------------------------------------------------------------------------

class FakeTaskSchedule:
    """Mimics TaskSchedule ORM row."""
    def __init__(self, id=1, name="patrol", cron_expression="*/3 * * * *",
                 workflow_definition_id=None, task_type="WORKFLOW",
                 enabled=True, next_run_at=None, device_ids=None,
                 tool_id=None, task_template_id=None, params=None,
                 target_device_id=None):
        self.id = id
        self.name = name
        self.cron_expression = cron_expression
        self.workflow_definition_id = workflow_definition_id
        self.task_type = task_type
        self.enabled = enabled
        self.next_run_at = next_run_at or datetime.utcnow()
        self.device_ids = device_ids or []
        self.tool_id = tool_id
        self.task_template_id = task_template_id
        self.params = params or {}
        self.target_device_id = target_device_id
        self.last_run_at = None


class FakeWorkflowRun:
    """Mimics WorkflowRun ORM row."""
    def __init__(self, id=1, workflow_definition_id=1, status="RUNNING",
                 started_at=None):
        self.id = id
        self.workflow_definition_id = workflow_definition_id
        self.status = status
        self.started_at = started_at or datetime.utcnow()


class FakeQuery:
    """Chainable mock query for SQLAlchemy-style calls."""
    def __init__(self, items=None, count_val=0):
        self._items = items or []
        self._count_val = count_val

    def filter(self, *args, **kwargs):
        return self

    def limit(self, n):
        return self

    def all(self):
        return self._items

    def count(self):
        return self._count_val


# ===========================================================================
# Tests: overlap protection in _fire_schedule
# ===========================================================================

class TestOverlapProtection:
    """CronScheduler._fire_schedule skips dispatch when RUNNING runs exist."""

    def test_skip_when_active_run_exists(self):
        """Should skip dispatch and only update next_run_at."""
        from backend.scheduler.cron_scheduler import CronScheduler

        now = datetime.utcnow()
        sched = FakeTaskSchedule(
            workflow_definition_id=42,
            next_run_at=now - timedelta(seconds=10),
        )

        db = MagicMock()
        db.query.return_value = FakeQuery(count_val=1)

        scheduler = CronScheduler()

        with patch("backend.scheduler.cron_scheduler._compute_next_run") as mock_next, \
             patch("backend.scheduler.cron_scheduler.asyncio.run") as mock_run:
            mock_next.return_value = now + timedelta(minutes=3)
            scheduler._fire_schedule(db, sched, now)

        assert sched.next_run_at == now + timedelta(minutes=3)
        mock_run.assert_not_called()

    def test_dispatch_when_no_active_runs(self):
        """Should dispatch normally when no RUNNING runs."""
        from backend.scheduler.cron_scheduler import CronScheduler

        now = datetime.utcnow()
        sched = FakeTaskSchedule(
            workflow_definition_id=42,
            device_ids=[1, 2],
            next_run_at=now - timedelta(seconds=10),
        )

        db = MagicMock()
        db.query.return_value = FakeQuery(count_val=0)

        scheduler = CronScheduler()

        with patch("backend.scheduler.cron_scheduler._compute_next_run") as mock_next, \
             patch("backend.scheduler.cron_scheduler.asyncio.run") as mock_run:
            mock_next.return_value = now + timedelta(minutes=3)
            scheduler._fire_schedule(db, sched, now)

        mock_run.assert_called_once()
        assert sched.last_run_at == now

    def test_stale_running_ignored(self):
        """RUNNING runs older than PATROL_TIMEOUT_MINUTES should not block dispatch."""
        from backend.scheduler.cron_scheduler import CronScheduler

        now = datetime.utcnow()
        sched = FakeTaskSchedule(
            workflow_definition_id=42,
            device_ids=[1],
            next_run_at=now - timedelta(seconds=10),
        )

        db = MagicMock()
        db.query.return_value = FakeQuery(count_val=0)

        scheduler = CronScheduler()

        with patch("backend.scheduler.cron_scheduler._compute_next_run") as mock_next, \
             patch("backend.scheduler.cron_scheduler.asyncio.run") as mock_run:
            mock_next.return_value = now + timedelta(minutes=3)
            scheduler._fire_schedule(db, sched, now)

        mock_run.assert_called_once()

    def test_overlap_check_error_skips_dispatch(self):
        """Fail-closed: if overlap query raises, skip dispatch to avoid concurrency risk."""
        from backend.scheduler.cron_scheduler import CronScheduler

        now = datetime.utcnow()
        sched = FakeTaskSchedule(
            workflow_definition_id=42,
            next_run_at=now - timedelta(seconds=10),
        )

        db = MagicMock()
        db.query.side_effect = Exception("DB connection lost")

        scheduler = CronScheduler()

        with patch("backend.scheduler.cron_scheduler._compute_next_run") as mock_next, \
             patch("backend.scheduler.cron_scheduler.asyncio.run") as mock_run:
            mock_next.return_value = now + timedelta(minutes=3)
            scheduler._fire_schedule(db, sched, now)

        # Dispatch must NOT happen
        mock_run.assert_not_called()
        # next_run_at should still be advanced
        assert sched.next_run_at == now + timedelta(minutes=3)
        # last_run_at should NOT be set (no dispatch occurred)
        assert sched.last_run_at is None


# ===========================================================================
# Tests: history run cleanup
# ===========================================================================

class TestCleanupOldRuns:
    """CronScheduler._cleanup_old_runs deletes terminal WorkflowRuns past retention."""

    def test_deletes_stale_runs(self):
        from backend.scheduler.cron_scheduler import CronScheduler

        now = datetime.utcnow()
        old_run = FakeWorkflowRun(
            id=99,
            status="SUCCESS",
            started_at=now - timedelta(days=5),
        )

        db = MagicMock()
        db.query.return_value = FakeQuery(items=[old_run])

        scheduler = CronScheduler()
        scheduler._cleanup_old_runs(db, now)

        db.delete.assert_called_once_with(old_run)
        db.commit.assert_called_once()

    def test_no_stale_runs_no_delete(self):
        from backend.scheduler.cron_scheduler import CronScheduler

        now = datetime.utcnow()
        db = MagicMock()
        db.query.return_value = FakeQuery(items=[])

        scheduler = CronScheduler()
        scheduler._cleanup_old_runs(db, now)

        db.delete.assert_not_called()

    def test_import_failure_does_not_crash(self):
        """If WorkflowRun import fails, cleanup should log warning and rollback."""
        from backend.scheduler.cron_scheduler import CronScheduler

        now = datetime.utcnow()
        db = MagicMock()
        db.query.side_effect = Exception("import simulation")

        scheduler = CronScheduler()
        scheduler._cleanup_old_runs(db, now)
        db.rollback.assert_called_once()
