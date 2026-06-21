## 1. Shared DeviceLockService (Phase 1 — Pure Refactor)

- [x] 1.1 Create `backend/services/device_lock.py` with `acquire_lock(db, device_id, job_id, lease_seconds) -> bool` using atomic conditional UPDATE
- [x] 1.2 Add `extend_lock(db, device_id, job_id, lease_seconds) -> bool` to DeviceLockService
- [x] 1.3 Add `release_lock(db, device_id, job_id) -> bool` to DeviceLockService
- [x] 1.4 Refactor `scheduler/recycler.py` `_release_device_lock` and `_check_device_lock_expiration` to call DeviceLockService
- [x] 1.5 Refactor `api/routes/agent_api.py` `extend_job_lock` endpoint to call `DeviceLockService.extend_lock`
- [x] 1.6 Refactor `scheduler/dispatcher.py` `_create_run_with_lock` to call `DeviceLockService.acquire_lock`
- [x] 1.7 Write unit tests for DeviceLockService (acquire free, acquire expired, acquire contested, extend valid, extend stolen, release valid, release wrong owner)

## 2. Workflow Dispatch Lock + Claim Guard (Phase 2)

- [x] 2.1 Integrate `acquire_lock` into `services/dispatcher.py` `dispatch_workflow`: attempt lock per device, log warning on contention, create JobInstance as PENDING regardless
- [x] 2.2 Add per-device deduplication to `agent_api.py` `get_pending_jobs`: group PENDING jobs by device_id, only claim earliest per device
- [x] 2.3 Add device lock check to `get_pending_jobs`: skip jobs whose target device has `lock_run_id` held by a different RUNNING job
- [x] 2.4 Wrap claim state transition + `acquire_lock` in a savepoint in `get_pending_jobs`: rollback job to PENDING if lock acquire fails
- [x] 2.5 Apply same per-device deduplication and lock check to `claim_jobs` endpoint
- [x] 2.6 Add lock release call to `complete_job` endpoint: call `release_lock(db, job.device_id, job_id)` on terminal state transition
- [x] 2.7 Add `_active_device_ids: set[int]` to Agent `main.py`: populate on job submit, check before thread pool submission, clear in `_run_task_wrapper` finally block
- [x] 2.8 Write integration tests: dispatch to busy device queues job, claim skips locked device, claim rolls back on lock race, complete_job releases lock

## 3. Session Watchdog Consolidation (Phase 3)

- [x] 3.1 Create `backend/tasks/session_watchdog.py` with async `session_watchdog_loop` running on configurable interval
- [x] 3.2 Implement host heartbeat timeout check: mark OFFLINE if `last_heartbeat` older than `HOST_HEARTBEAT_TIMEOUT_SECONDS` (default 120s), transition RUNNING jobs to UNKNOWN
- [x] 3.3 Implement device lock expiration check: call `release_lock` for expired locks, transition associated job to UNKNOWN
- [x] 3.4 Implement UNKNOWN grace period check: transition UNKNOWN jobs older than `UNKNOWN_GRACE_SECONDS` (default 300s) to FAILED, call `WorkflowAggregator.on_job_terminal`
- [x] 3.5 Add `USE_SESSION_WATCHDOG` env var (default `true`); wire watchdog into app lifespan in `backend/main.py`
- [x] 3.6 Guard recycler's `_check_host_heartbeat_timeout` and `_check_device_lock_expiration` with `USE_SESSION_WATCHDOG` flag: skip when watchdog is enabled
- [x] 3.7 Write tests for watchdog: host timeout marks OFFLINE + jobs UNKNOWN, expired lock released + job UNKNOWN, grace period expiry → FAILED, recovery UNKNOWN → RUNNING not blocked

## 4. PipelineEngine Lock Hooks (Phase 4)

- [x] 4.1 Add lock verification at pipeline start in `pipeline_engine.py`: call `extend_lock` endpoint before executing stages, abort with `device_lock_not_held` on 409
- [x] 4.2 Add retry logic (3 attempts, exponential backoff) for lock verification network failures, abort with `lock_verification_unreachable` if all fail
- [x] 4.3 Add lock-lost detection at step boundaries: if `LockRenewalManager` removes run from `_active_run_ids` (409), engine aborts current pipeline with status ABORTED
- [x] 4.4 Write tests for PipelineEngine: start with valid lock proceeds, start with stolen lock aborts, lock lost mid-pipeline aborts at next step boundary

## 5. Documentation and Cleanup

- [x] 5.1 Update Agent `.env.example` with new env vars: `HOST_HEARTBEAT_TIMEOUT_SECONDS`, `UNKNOWN_GRACE_SECONDS`, `USE_SESSION_WATCHDOG`
- [x] 5.2 Update `backend/tasks/heartbeat_monitor.py` docstring noting it is superseded by session_watchdog when `USE_SESSION_WATCHDOG=true`
