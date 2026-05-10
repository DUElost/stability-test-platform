"""Abort reaper integration tests — ADR-0021 v3 §P1.

Requires PostgreSQL (::timestamptz cast).  Skips on SQLite.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL", "").startswith("postgresql"),
    reason="abort reaper SQL uses PG-native JSONB ::timestamptz cast",
)


@pytest.fixture
def _seed_abort_context(db_session, sample_plan_run, sample_device, sample_host):
    """Create PlanRun.run_context with abort_requested.at set to a given age."""
    from backend.models.plan_run import PlanRun
    from sqlalchemy.orm.attributes import flag_modified

    def _seed(age_seconds: int):
        at = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
        run = db_session.get(PlanRun, sample_plan_run.id)
        run.run_context = {"abort_requested": {"at": at.isoformat(), "reason": "test"}}
        flag_modified(run, "run_context")
        db_session.flush()
        return run

    return _seed


class TestAbortReaper:
    async def test_grace_not_expired_job_untouched(
        self, db_session, sample_plan_run, sample_running_job, _seed_abort_context,
    ):
        """grace 未到, job 不动"""
        from backend.scheduler.device_lease_reconciler import (
            _reconcile_aborted_running_jobs,
        )

        _seed_abort_context(age_seconds=30)  # grace=60s default

        count, items = await _reconcile_aborted_running_jobs(db_session)

        assert count == 0
        assert items == []
        db_session.refresh(sample_running_job)
        assert sample_running_job.status == "RUNNING"
        assert sample_running_job.ended_at is None

    async def test_grace_expired_job_transitions_to_aborted(
        self, db_session, sample_plan_run, sample_running_job, _seed_abort_context,
    ):
        """grace 已到, job 转 ABORTED"""
        from backend.scheduler.device_lease_reconciler import (
            _reconcile_aborted_running_jobs,
        )

        _seed_abort_context(age_seconds=90)  # grace=60s default, exceeded

        count, items = await _reconcile_aborted_running_jobs(db_session)

        assert count == 1
        assert len(items) == 1
        assert items[0]["type"] == "job_status"
        assert items[0]["job_id"] == sample_running_job.id
        assert items[0]["status"] == "ABORTED"

        db_session.refresh(sample_running_job)
        assert sample_running_job.status == "ABORTED"
        assert sample_running_job.ended_at is not None
        assert sample_running_job.status_reason == "aborted_reaper_timeout"

    async def test_plan_run_failed_on_abort_v3_override(
        self, db_session, sample_plan_run, sample_plan, sample_device, sample_host, _seed_abort_context,
    ):
        """PlanRun 转 FAILED(v3 §P4: abort 覆盖 threshold)"""
        from backend.models.job import JobInstance
        from backend.models.enums import JobStatus
        from backend.scheduler.device_lease_reconciler import (
            _reconcile_aborted_running_jobs,
        )

        # 3 jobs: 1 completed, 1 failed, 1 running (will be aborted)
        jobs = []
        for status in [JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.RUNNING]:
            j = JobInstance(
                plan_run_id=sample_plan_run.id,
                plan_id=sample_plan.id,
                device_id=sample_device.id,
                host_id=sample_host.id,
                status=status.value,
                pipeline_def={"lifecycle": {"init": [], "teardown": []}},
            )
            db_session.add(j)
            db_session.flush()
            jobs.append(j)

        _seed_abort_context(age_seconds=90)

        count, _ = await _reconcile_aborted_running_jobs(db_session)

        assert count == 1
        db_session.refresh(sample_plan_run)
        assert sample_plan_run.status == "FAILED"
        assert sample_plan_run.result_summary is not None
        assert sample_plan_run.result_summary["total"] == 3
        assert sample_plan_run.result_summary["aborted"] == 1
        assert sample_plan_run.result_summary["failed_only"] == 1

    async def test_lingering_active_lease_released_on_abort(
        self, db_session, sample_plan_run, sample_running_job, sample_device, _seed_abort_context,
    ):
        """残留 ACTIVE lease 被防御性释放"""
        from backend.models.device_lease import DeviceLease
        from backend.models.enums import LeaseStatus, LeaseType
        from backend.scheduler.device_lease_reconciler import (
            _reconcile_aborted_running_jobs,
        )

        lease = DeviceLease(
            device_id=sample_device.id,
            job_id=sample_running_job.id,
            host_id=sample_device.host_id,
            lease_type=LeaseType.JOB.value,
            status=LeaseStatus.ACTIVE.value,
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=600),
        )
        db_session.add(lease)
        db_session.flush()

        _seed_abort_context(age_seconds=90)

        count, _ = await _reconcile_aborted_running_jobs(db_session)

        assert count == 1
        db_session.refresh(lease)
        assert lease.status == LeaseStatus.RELEASED.value
