## ADDED Requirements

### Requirement: DeviceLockService acquire operation
The system SHALL provide an `acquire_lock(db, device_id, job_id, lease_seconds)` function in `backend/services/device_lock.py` that atomically acquires a device lock for a job. The function SHALL execute a conditional `UPDATE device SET lock_run_id = :job_id, lock_expires_at = :now + :lease, status = 'BUSY' WHERE id = :device_id AND (lock_run_id IS NULL OR lock_expires_at < :now)` and return `True` if the row was updated, `False` otherwise.

#### Scenario: Acquire lock on free device
- **WHEN** `acquire_lock` is called for a device where `lock_run_id IS NULL`
- **THEN** the function SHALL set `lock_run_id` to the job ID, `lock_expires_at` to `now + lease_seconds`, `status` to `BUSY`, and return `True`

#### Scenario: Acquire lock on device with expired lease
- **WHEN** `acquire_lock` is called for a device where `lock_run_id IS NOT NULL` but `lock_expires_at < now`
- **THEN** the function SHALL overwrite `lock_run_id` with the new job ID, reset `lock_expires_at`, and return `True`

#### Scenario: Acquire lock on device locked by another active job
- **WHEN** `acquire_lock` is called for a device where `lock_run_id` belongs to another job and `lock_expires_at >= now`
- **THEN** the function SHALL NOT modify the device row and SHALL return `False`

#### Scenario: Acquire lock idempotent re-acquire
- **WHEN** `acquire_lock` is called for a device where `lock_run_id` already equals the given `job_id`
- **THEN** the function SHALL extend `lock_expires_at` and return `True`

### Requirement: DeviceLockService extend operation
The system SHALL provide an `extend_lock(db, device_id, job_id, lease_seconds)` function that extends the lease for an existing lock. The function SHALL only succeed if `device.lock_run_id == job_id`.

#### Scenario: Extend lock held by this job
- **WHEN** `extend_lock` is called and `device.lock_run_id == job_id`
- **THEN** the function SHALL set `lock_expires_at` to `now + lease_seconds` and return `True`

#### Scenario: Extend lock held by different job
- **WHEN** `extend_lock` is called and `device.lock_run_id != job_id`
- **THEN** the function SHALL NOT modify the device row and SHALL return `False`

#### Scenario: Extend lock on unlocked device
- **WHEN** `extend_lock` is called and `device.lock_run_id IS NULL`
- **THEN** the function SHALL return `False` without modification

### Requirement: DeviceLockService release operation
The system SHALL provide a `release_lock(db, device_id, job_id)` function that releases a device lock. The function SHALL only clear the lock if `device.lock_run_id == job_id`, preventing a job from releasing another job's lock.

#### Scenario: Release lock held by this job
- **WHEN** `release_lock` is called and `device.lock_run_id == job_id`
- **THEN** the function SHALL set `lock_run_id` to `NULL`, `lock_expires_at` to `NULL`, and `status` to `ONLINE` (if it was `BUSY`), and return `True`

#### Scenario: Release lock held by different job
- **WHEN** `release_lock` is called and `device.lock_run_id != job_id`
- **THEN** the function SHALL NOT modify the device row and SHALL return `False`

#### Scenario: Release lock on already-unlocked device
- **WHEN** `release_lock` is called and `device.lock_run_id IS NULL`
- **THEN** the function SHALL return `False` (no-op)

### Requirement: Lock acquire at workflow dispatch time
The workflow dispatcher (`services/dispatcher.py`) SHALL attempt to acquire a device lock when creating JobInstances. If the device is already locked by another active job, the JobInstance SHALL still be created with status `PENDING` (queued for later execution).

#### Scenario: Dispatch to free device
- **WHEN** `dispatch_workflow` creates a JobInstance for a device where `lock_run_id IS NULL`
- **THEN** the dispatcher SHALL call `acquire_lock` and, on success, the device becomes `BUSY` with the new job holding the lock

#### Scenario: Dispatch to busy device
- **WHEN** `dispatch_workflow` creates a JobInstance for a device where another job holds the lock
- **THEN** the dispatcher SHALL log a warning, create the JobInstance with status `PENDING`, and continue without failing the workflow

### Requirement: Lock acquire at job claim time
The Agent claim endpoint (`GET /api/v1/agent/jobs/pending`) SHALL acquire the device lock atomically when transitioning a job from `PENDING` to `RUNNING`. If the lock cannot be acquired, the job SHALL be skipped (left as `PENDING`).

#### Scenario: Claim job for unlocked device
- **WHEN** the claim endpoint processes a PENDING job whose target device has no active lock
- **THEN** it SHALL transition the job to `RUNNING`, call `acquire_lock`, and include the job in the response

#### Scenario: Claim job for locked device
- **WHEN** the claim endpoint processes a PENDING job whose target device is locked by a different job
- **THEN** it SHALL skip the job (leave it `PENDING`) and NOT include it in the response

#### Scenario: Lock acquire failure rolls back claim
- **WHEN** the claim endpoint transitions a job to `RUNNING` but `acquire_lock` returns `False` (race condition)
- **THEN** the endpoint SHALL roll back the state transition (job stays `PENDING`) using a database savepoint

### Requirement: Lock release on job completion
The job completion endpoint (`POST /api/v1/agent/jobs/{job_id}/complete`) SHALL release the device lock when a job reaches a terminal state.

#### Scenario: Job completes successfully
- **WHEN** the `complete_job` endpoint transitions a job to `COMPLETED`
- **THEN** it SHALL call `release_lock(db, job.device_id, job_id)` to free the device

#### Scenario: Job fails
- **WHEN** the `complete_job` endpoint transitions a job to `FAILED`
- **THEN** it SHALL call `release_lock(db, job.device_id, job_id)` to free the device

#### Scenario: Job aborted
- **WHEN** the `complete_job` endpoint transitions a job to `ABORTED`
- **THEN** it SHALL call `release_lock(db, job.device_id, job_id)` to free the device

### Requirement: Session watchdog background task
The system SHALL run a single consolidated background task (`backend/tasks/session_watchdog.py`) that handles host heartbeat timeout, device lock expiration, and UNKNOWN job grace period.

#### Scenario: Host heartbeat timeout
- **WHEN** a host's `last_heartbeat` is older than `HOST_HEARTBEAT_TIMEOUT_SECONDS` (default 120s) and its status is `ONLINE`
- **THEN** the watchdog SHALL set the host status to `OFFLINE` and transition all its RUNNING jobs to `UNKNOWN`

#### Scenario: Device lock expiration
- **WHEN** a device has `lock_run_id IS NOT NULL` and `lock_expires_at < now`
- **THEN** the watchdog SHALL call `release_lock` for that device and transition the associated job to `UNKNOWN` (if it was `RUNNING`)

#### Scenario: UNKNOWN job grace period expiration
- **WHEN** a job has been in `UNKNOWN` status for longer than `UNKNOWN_GRACE_SECONDS` (default 300s)
- **THEN** the watchdog SHALL transition the job to `FAILED` with reason `unknown_grace_timeout` and trigger `WorkflowAggregator.on_job_terminal`

#### Scenario: UNKNOWN job recovery
- **WHEN** an Agent reconnects and claims a job that was in `UNKNOWN` status (within grace period)
- **THEN** the `JobStateMachine` SHALL allow the `UNKNOWN → RUNNING` transition, and the watchdog SHALL NOT interfere with the recovered job

### Requirement: Watchdog feature flag
The session watchdog SHALL be controlled by the `USE_SESSION_WATCHDOG` environment variable (default `true`). When enabled, the legacy recycler's overlapping checks (host heartbeat timeout and device lock expiration) SHALL be skipped.

#### Scenario: Watchdog enabled (default)
- **WHEN** `USE_SESSION_WATCHDOG` is `true` or unset
- **THEN** the watchdog background task SHALL run, and the recycler SHALL skip `_check_host_heartbeat_timeout` and `_check_device_lock_expiration`

#### Scenario: Watchdog disabled (rollback)
- **WHEN** `USE_SESSION_WATCHDOG` is `false`
- **THEN** the watchdog background task SHALL NOT start, and the recycler SHALL run all its checks as before

### Requirement: Unified extend_lock endpoint
The existing `POST /api/v1/agent/jobs/{job_id}/extend_lock` endpoint SHALL use the shared `DeviceLockService.extend_lock` function and work for both legacy TaskRun and Workflow JobInstance paths.

#### Scenario: Extend lock for workflow job
- **WHEN** the Agent sends an extend_lock request for a workflow JobInstance
- **THEN** the endpoint SHALL call `extend_lock(db, device_id, job_id, DEVICE_LOCK_LEASE_SECONDS)` and return the new expiry time

#### Scenario: Lock stolen by watchdog
- **WHEN** the Agent sends an extend_lock request but the watchdog has already released the lock (expired)
- **THEN** the endpoint SHALL return HTTP 409 and the Agent's `LockRenewalManager` SHALL remove the run from `_active_run_ids`
