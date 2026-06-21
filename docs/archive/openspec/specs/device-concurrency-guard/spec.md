## ADDED Requirements

### Requirement: Backend claim endpoint per-device filtering
The Agent claim endpoint (`GET /api/v1/agent/jobs/pending`) SHALL NOT claim multiple PENDING jobs targeting the same device in a single response. When multiple PENDING jobs exist for the same device, only the earliest-created job SHALL be eligible for claim.

#### Scenario: Two pending jobs for same device
- **WHEN** the claim endpoint finds two PENDING jobs both targeting device_id=5
- **THEN** it SHALL only attempt to claim the job with the earlier `created_at` timestamp; the second job SHALL remain `PENDING`

#### Scenario: Pending jobs for different devices
- **WHEN** the claim endpoint finds PENDING jobs targeting device_id=5 and device_id=7
- **THEN** it SHALL attempt to claim one job per device (up to the capacity limit)

#### Scenario: Device already locked by running job
- **WHEN** the claim endpoint processes a PENDING job whose target device has `lock_run_id` set to a different RUNNING job
- **THEN** it SHALL skip that job entirely, leaving it `PENDING` for a future poll cycle

### Requirement: Agent per-device execution guard
The Agent main loop SHALL maintain a local `_active_device_ids` set (parallel to `_active_run_ids`) that tracks which devices currently have a job executing in the thread pool. Before submitting a claimed job to the thread pool, the Agent SHALL check this set.

#### Scenario: Device not active locally
- **WHEN** the Agent receives a claimed job for device_id=5 and device_id=5 is NOT in `_active_device_ids`
- **THEN** the Agent SHALL add device_id=5 to `_active_device_ids` and submit the job to the thread pool

#### Scenario: Device already active locally
- **WHEN** the Agent receives a claimed job for device_id=5 and device_id=5 IS in `_active_device_ids`
- **THEN** the Agent SHALL skip submitting this job to the thread pool (the job remains RUNNING on the backend but is not double-executed locally)

#### Scenario: Device released after job completion
- **WHEN** a job for device_id=5 finishes (success or failure) in `_run_task_wrapper`
- **THEN** the `finally` block SHALL remove device_id=5 from `_active_device_ids`, allowing future jobs for that device to execute

### Requirement: Effective concurrency is min(max_concurrent_tasks, device_count)
The Agent's actual parallel execution capacity SHALL be the minimum of the configured `max_concurrent_tasks` and the number of distinct devices with pending work. The per-device guard ensures each device runs at most one job at a time.

#### Scenario: More slots than devices
- **WHEN** `max_concurrent_tasks=8` but the host has only 3 devices with pending jobs
- **THEN** the Agent SHALL run at most 3 jobs concurrently (one per device)

#### Scenario: More devices than slots
- **WHEN** `max_concurrent_tasks=2` and the host has 5 devices with pending jobs
- **THEN** the Agent SHALL run at most 2 jobs concurrently (limited by thread pool), with remaining devices queued
