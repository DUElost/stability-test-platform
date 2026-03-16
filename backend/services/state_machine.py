from __future__ import annotations

from datetime import datetime

from backend.models.enums import JobStatus
from backend.models.job import JobInstance

VALID_TRANSITIONS: dict[JobStatus, set[JobStatus]] = {
    JobStatus.PENDING:      {JobStatus.RUNNING},
    JobStatus.RUNNING:      {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.ABORTED, JobStatus.UNKNOWN},
    JobStatus.UNKNOWN:      {JobStatus.RUNNING, JobStatus.COMPLETED, JobStatus.FAILED},
    JobStatus.FAILED:       set(),
    JobStatus.COMPLETED:    set(),
    JobStatus.ABORTED:      set(),
    JobStatus.PENDING_TOOL: {JobStatus.PENDING},
}


class InvalidTransitionError(Exception):
    pass


class JobStateMachine:
    @staticmethod
    def transition(job: JobInstance, new_status: JobStatus, reason: str = "") -> None:
        try:
            current = JobStatus(job.status)
        except ValueError:
            raise InvalidTransitionError(f"Unknown job status '{job.status}' for job {job.id}")
        if new_status not in VALID_TRANSITIONS[current]:
            raise InvalidTransitionError(
                f"Cannot transition {job.status} -> {new_status} for job {job.id}"
            )
        job.status = new_status.value
        job.status_reason = reason
        job.updated_at = datetime.utcnow()
