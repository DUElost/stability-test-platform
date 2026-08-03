"""Unit tests for JobStateMachine terminal-state hygiene (issue #116).

Pure in-memory SimpleNamespace stand-ins, no DB required — mirrors
``test_plan_run_state_machine.py`` style.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from backend.models.enums import JobStatus
from backend.services.state_machine import JobStateMachine


def _job(status: JobStatus, *, execution_state="EXECUTING_STEP") -> SimpleNamespace:
    return SimpleNamespace(
        id=1,
        status=status.value,
        status_reason=None,
        execution_state=execution_state,
        updated_at=datetime.now(timezone.utc),
    )


@pytest.mark.parametrize(
    "target",
    [JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.ABORTED],
)
def test_terminal_transition_clears_execution_state(target):
    """进入终态必须清 execution_state（#116）——残留会污染并发统计。"""
    job = _job(JobStatus.RUNNING)
    JobStateMachine.transition(job, target, "reason")
    assert job.status == target.value
    assert job.execution_state is None


def test_non_terminal_transition_keeps_execution_state():
    """UNKNOWN 不是终态（grace 内可恢复 RUNNING），子状态必须保留。"""
    job = _job(JobStatus.RUNNING)
    JobStateMachine.transition(job, JobStatus.UNKNOWN, "lease_expired")
    assert job.status == JobStatus.UNKNOWN.value
    assert job.execution_state == "EXECUTING_STEP"


def test_recovery_to_running_keeps_clean_state():
    """UNKNOWN → RUNNING（recovery）不清也不补 execution_state；后续由
    extend-batch 重新上报。"""
    job = _job(JobStatus.UNKNOWN, execution_state=None)
    JobStateMachine.transition(job, JobStatus.RUNNING, "recovery_sync")
    assert job.status == JobStatus.RUNNING.value
    assert job.execution_state is None
