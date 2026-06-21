## Why

The new Workflow/JobInstance dispatch path (`backend/services/dispatcher.py`) creates jobs without acquiring device locks, leaving no guard against concurrent access to the same Android device. The legacy TaskRun path has a working lease mechanism (`Device.lock_run_id` + `lock_expires_at`), but the two systems are disconnected — the Agent can run multiple jobs on the same device simultaneously, lock renewal only functions for legacy runs, and two separate heartbeat-timeout detectors (recycler at 300s vs heartbeat_monitor at 30s) compete with different recovery strategies. A unified session-lease lifecycle is needed to close these gaps before the platform scales beyond a handful of devices.

## What Changes

- **Unified device-lock acquire** at dispatch time for both legacy TaskRun and Workflow JobInstance paths, using the existing `lock_run_id` / `lock_expires_at` columns with a shared helper.
- **Per-device concurrency guard on the Agent** — the claim endpoint and Agent main loop enforce at most one active job per device (not just a host-level `max_concurrent_tasks`).
- **Session lifecycle API** — new endpoints to explicitly start, renew, and release a "test session" tied to a (job, device) pair, replacing the implicit lock scattered across multiple routes.
- **Lease renewal unification** — `LockRenewalManager` works for both run types; the `extend_lock` endpoint validates against job ID regardless of origin.
- **Timeout / recovery consolidation** — merge the legacy recycler's lock-expiration check and the new heartbeat monitor into a single background task with consistent thresholds and a clear RUNNING → UNKNOWN → (recover | FAILED) path.
- **Queueing on contention** — when a device is locked, new jobs targeting that device remain PENDING instead of silently proceeding; the Agent skips them until the lock is released or expires.

## Capabilities

### New Capabilities
- `session-lifecycle`: Defines the API contract and state transitions for a test session (acquire → heartbeat/renew → release/expire), including the unified device-lock helper and the consolidated timeout recovery task.
- `device-concurrency-guard`: Per-device slot enforcement on both the backend claim endpoint and the Agent polling loop, ensuring a device runs at most one job at a time.

### Modified Capabilities
- `pipeline-engine`: The engine must call session-lifecycle acquire/release hooks at pipeline start/end, and feed heartbeat signals during execution.
- `step-tracking`: StepTrace events should reference the active session ID so that the reconciler can reconstruct session state after agent reconnection.

## Impact

- **Backend API** — `agent_api.py` (claim + extend_lock + complete), `services/dispatcher.py` (workflow dispatch), `scheduler/recycler.py` (lock expiration), `tasks/heartbeat_monitor.py` (timeout detection).
- **Agent** — `main.py` (LockRenewalManager, main loop per-device guard), `pipeline_engine.py` (session hooks).
- **Database** — No new tables; reuses `Device.lock_run_id` / `lock_expires_at`. May add a `session_id` column to `JobInstance` for traceability.
- **Existing tests** — `backend/agent/test_agent.py` needs updates for the per-device guard. Workflow dispatch tests need lock-acquire assertions.
- **No frontend impact** — session lifecycle is entirely backend/agent; the frontend already reflects device status via heartbeat-driven `BUSY`/`ONLINE`.
