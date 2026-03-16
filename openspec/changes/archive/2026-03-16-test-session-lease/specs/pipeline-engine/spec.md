## ADDED Requirements

### Requirement: Lock verification at pipeline start
The `PipelineEngine` SHALL verify that the device lock is held by the current job before executing any pipeline stages. Verification SHALL be performed by calling the `extend_lock` endpoint as a liveness check.

#### Scenario: Lock held by this job
- **WHEN** the pipeline engine starts and calls `extend_lock` for the current job
- **THEN** the extend succeeds, and the engine SHALL proceed with stage execution

#### Scenario: Lock not held (stolen or expired)
- **WHEN** the pipeline engine starts and calls `extend_lock` but receives a 409 response
- **THEN** the engine SHALL abort immediately with error `device_lock_not_held`, skip all stages, and report the job as FAILED

#### Scenario: Lock verification network failure
- **WHEN** the pipeline engine starts and the `extend_lock` call fails due to a network error
- **THEN** the engine SHALL retry up to 3 times with exponential backoff; if all retries fail, the engine SHALL abort with error `lock_verification_unreachable`

### Requirement: Lock release via completion endpoint
The `PipelineEngine` SHALL NOT call `release_lock` directly. Lock release SHALL occur server-side when the Agent reports job completion via `POST /api/v1/agent/jobs/{job_id}/complete`.

#### Scenario: Pipeline completes successfully
- **WHEN** the pipeline engine finishes all stages without fatal errors
- **THEN** it SHALL call `complete_run` with status `COMPLETED`, and the backend SHALL release the device lock

#### Scenario: Pipeline fails
- **WHEN** the pipeline engine encounters a fatal step failure
- **THEN** it SHALL call `complete_run` with status `FAILED`, and the backend SHALL release the device lock

#### Scenario: Pipeline aborted (lock lost mid-execution)
- **WHEN** the `LockRenewalManager` receives a 409 during periodic renewal (lock stolen)
- **THEN** it SHALL remove the run from `_active_run_ids`, and the pipeline engine SHALL detect the lost lock at the next step boundary and abort with status `ABORTED`
