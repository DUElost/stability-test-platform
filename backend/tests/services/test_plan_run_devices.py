"""#1520 垂直切片：PlanRun devices 矩阵状态派生服务层直测。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from backend.models.enums import JobStatus
from backend.services.plan_run_devices import (
    current_stage_for_job,
    job_exec_status_for_job,
    pending_claim_deadline,
    ui_status_for_job,
)


def _job(**kwargs):
    defaults = dict(
        status=JobStatus.RUNNING.value,
        manual_action=None,
        next_retry_at=None,
        patrol_cycle_count=0,
        last_patrol_heartbeat_at=None,
        created_at=None,
        started_at=None,
        ended_at=None,
        execution_state=None,
        last_execution_heartbeat_at=None,
        host_id="h1",
        current_patrol_step=None,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


class TestJobExecStatus:
    def test_terminal_and_pending(self):
        now = datetime(2026, 9, 17, tzinfo=timezone.utc)
        assert job_exec_status_for_job(_job(status=JobStatus.COMPLETED.value), now) == "completed"
        assert job_exec_status_for_job(_job(status=JobStatus.FAILED.value), now) == "failed"
        assert job_exec_status_for_job(_job(status=JobStatus.PENDING.value), now) == "pending"
        assert job_exec_status_for_job(_job(status=JobStatus.UNKNOWN.value), now) == "unknown"

    def test_backoff_on_exit_or_retry(self):
        now = datetime(2026, 9, 17, 12, 0, tzinfo=timezone.utc)
        assert (
            job_exec_status_for_job(
                _job(manual_action="EXIT_REQUESTED"), now
            )
            == "backoff"
        )
        assert (
            job_exec_status_for_job(
                _job(next_retry_at=now + timedelta(minutes=5)), now
            )
            == "backoff"
        )
        assert job_exec_status_for_job(_job(), now) == "running"


class TestUiStatus:
    def test_disconnected_running_becomes_unknown(self):
        now = datetime(2026, 9, 17, tzinfo=timezone.utc)
        device = SimpleNamespace(
            adb_connected=False,
            adb_state="offline",
            status="OFFLINE",
        )
        assert ui_status_for_job(_job(), now, device, "ONLINE") == "unknown"


class TestCurrentStageAndPendingDeadline:
    def test_patrol_vs_init(self):
        assert current_stage_for_job(_job(patrol_cycle_count=1)) == "patrol"
        assert current_stage_for_job(_job()) == "init"

    def test_pending_deadline(self):
        created = datetime(2026, 9, 17, 10, 0, tzinfo=timezone.utc)
        deadline = pending_claim_deadline(
            _job(status=JobStatus.PENDING.value, created_at=created)
        )
        assert deadline is not None
        assert deadline > created
        assert pending_claim_deadline(_job()) is None
