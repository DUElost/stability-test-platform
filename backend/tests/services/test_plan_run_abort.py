import pytest
from backend.services.state_machine import JobStateMachine, InvalidTransitionError
from backend.models.enums import JobStatus
from backend.models.job import JobInstance


def test_pending_to_aborted_is_valid():
    """PENDING->ABORTED is now a valid transition (via plan_run_abort flow)."""
    job = JobInstance(
        status=JobStatus.PENDING.value, plan_run_id=1, plan_id=1, device_id=1
    )
    JobStateMachine.transition(job, JobStatus.ABORTED, "aborted_by_user")
    assert job.status == JobStatus.ABORTED.value
    assert job.status_reason == "aborted_by_user"


def test_aborted_is_terminal():
    """ABORTED is terminal, cannot transition from it."""
    job = JobInstance(
        status=JobStatus.ABORTED.value, plan_run_id=1, plan_id=1, device_id=1
    )
    with pytest.raises(InvalidTransitionError):
        JobStateMachine.transition(job, JobStatus.RUNNING, "recover")


def test_running_to_aborted_is_valid():
    """RUNNING->ABORTED already exists in abort flow, ensure not broken."""
    job = JobInstance(
        status=JobStatus.RUNNING.value, plan_run_id=1, plan_id=1, device_id=1
    )
    JobStateMachine.transition(job, JobStatus.ABORTED, "aborted_by_user")
    assert job.status == JobStatus.ABORTED.value
