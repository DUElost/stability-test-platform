## Context

The platform has two dispatch paths — a legacy `TaskRun` path (via `scheduler/dispatcher.py`) and a newer `WorkflowRun/JobInstance` path (via `services/dispatcher.py`). The legacy path implements a working device-lock lease (atomically sets `Device.lock_run_id` + `lock_expires_at` at dispatch, renewed every 60s by the Agent's `LockRenewalManager`, recovered by the `recycler`). The workflow path skips all of this: it creates PENDING jobs without checking or acquiring device locks, the Agent's claim endpoint (`GET /jobs/pending`) claims all pending jobs for a host regardless of per-device state, and the main loop's concurrency limit is host-level only — two jobs targeting the same device can execute in parallel.

Additionally, two background tasks compete on host-timeout detection: the legacy recycler (300s threshold, marks `FAILED`) and the async heartbeat monitor (30s threshold, marks `UNKNOWN`). Both run independently with no coordination.

Key constraints:
- Device model already has `lock_run_id` (Integer) and `lock_expires_at` (DateTime) columns — reuse them.
- The `LockRenewalManager` in the Agent already implements retry + backoff for `extend_lock` calls.
- The `JobStateMachine` already supports an `UNKNOWN` recovery state (`UNKNOWN → RUNNING | COMPLETED`).
- All new API endpoints must use the `ApiResponse[T]` wrapper and `X-Agent-Secret` authentication.

## Goals / Non-Goals

**Goals:**
- Unified device lock acquire/release for both legacy TaskRun and Workflow JobInstance paths, using a single shared helper.
- Per-device concurrency guard: at most one active job per device, enforced at both the backend claim endpoint and the Agent polling loop.
- Consolidated timeout/recovery: a single background task handles host heartbeat timeout, device lock expiration, and stuck-job detection, with consistent thresholds across both paths.
- Lease lifecycle is explicit: acquire at dispatch/claim, renew periodically, release on completion/failure, expire on timeout.

**Non-Goals:**
- Multi-job-per-device scheduling (e.g. "light" jobs that can share a device) — out of scope; strict 1:1.
- New database tables or a separate `Session` ORM model — reuse existing `Device.lock_run_id` / `lock_expires_at`.
- Frontend changes — device `BUSY`/`ONLINE` status is already derived from `lock_run_id` in the heartbeat route.
- Legacy TaskRun path removal — both paths continue to work; they just share the same lock mechanism.
- Queue priority or fairness algorithms for contended devices.

## Decisions

### D1: Shared `DeviceLockService` helper (not raw SQL in each route)

Extract a `backend/services/device_lock.py` module with three async functions:

- `acquire_lock(db, device_id, job_id, lease_seconds) → bool` — atomic `UPDATE ... WHERE lock_run_id IS NULL OR lock_expires_at < now`, returns `True` if acquired.
- `extend_lock(db, device_id, job_id, lease_seconds) → bool` — extends `lock_expires_at` only if `lock_run_id == job_id`.
- `release_lock(db, device_id, job_id) → bool` — clears `lock_run_id`/`lock_expires_at`, restores status to `ONLINE` if it was `BUSY`.

**Why:** The same lock logic is currently duplicated across `scheduler/dispatcher.py` (raw SQL), `tasks.py` (`_acquire_device_lock`), `agent_api.py` (`extend_job_lock`), and `recycler.py` (`_release_device_lock`). A single service eliminates divergence and makes behavior testable.

**Alternative considered:** An ORM-level `@event.listens_for` hook on `Device.lock_run_id` changes — rejected because lock acquire must be atomic at the SQL level (`FOR UPDATE SKIP LOCKED` or conditional `UPDATE`), which doesn't map cleanly to ORM events.

### D2: Lock acquire at two points in the workflow path

1. **At dispatch time** (`services/dispatcher.py`): Before creating a `JobInstance` for a device, call `acquire_lock`. If the device is locked, the job is still created with status `PENDING` but skipped for immediate dispatch — this is the "queueing on contention" behavior. The dispatcher logs a warning but does not fail the workflow.

2. **At claim time** (`agent_api.py`, `get_pending_jobs`): Before transitioning a job to `RUNNING`, verify the device is not locked by a *different* job. If locked, skip that job (leave it `PENDING`). If unlocked, acquire the lock atomically. This is the hard guard — even if the dispatcher didn't lock, the claim endpoint ensures exclusivity.

**Why two points:** Dispatch-time locking is optimistic (a device may become free before the Agent polls). Claim-time locking is the authoritative guard. Together they prevent both dispatch-level and execution-level conflicts.

**Alternative considered:** Lock only at claim time — simpler, but the dispatcher would have no visibility into device contention, making capacity planning harder. The dual approach also matches the legacy path's behavior.

### D3: Per-device filter in the Agent main loop

The Agent's `fetch_pending_runs` already returns claimed jobs. Add a local `_active_device_ids: set[int]` (parallel to `_active_run_ids`). Before submitting a job to the thread pool, check if `job["device_id"]` is already in `_active_device_ids`. If so, skip it (don't execute, don't add to active set — the backend will serve it again on the next poll since it's still RUNNING but the Agent won't double-execute). Remove the device ID from `_active_device_ids` in the `finally` block of `_run_task_wrapper`.

**Why client-side too:** The backend guard is authoritative, but the Agent can claim multiple jobs for the same device in a single `get_pending_jobs` response (the endpoint doesn't deduplicate per-device). A local set prevents the thread pool from starting two jobs for the same device within the same poll cycle.

### D4: Consolidate timeout detection into a single async background task

Merge the recycler's host-timeout + device-lock-expiration logic and the heartbeat monitor's job-UNKNOWN logic into one `backend/tasks/session_watchdog.py`:

| Check | Threshold (env var) | Default | Action |
|---|---|---|---|
| Host heartbeat timeout | `HOST_HEARTBEAT_TIMEOUT_SECONDS` | 120s | Mark host `OFFLINE` |
| Device lock expiration | `DEVICE_LOCK_LEASE_SECONDS` | 600s | Release lock; mark job `UNKNOWN` |
| UNKNOWN job grace period | `UNKNOWN_GRACE_SECONDS` | 300s | If still `UNKNOWN` after grace period, transition to `FAILED` |

The legacy recycler's `_check_host_heartbeat_timeout` (300s) and the heartbeat monitor's 30s threshold are both too extreme. 120s is a reasonable middle ground — it tolerates network blips but doesn't leave a device locked for 5 minutes after an Agent crash.

**Recovery path:** RUNNING → UNKNOWN (host timeout) → RUNNING (agent reconnects and claims again) or → FAILED (grace period expires). This reuses the existing `UNKNOWN` state in the `JobStateMachine`.

**Alternative considered:** Keep two separate tasks — rejected because the overlapping scope (both detect dead hosts and mark jobs) creates race conditions and conflicting thresholds.

### D5: Lock acquire hooks in PipelineEngine

The `PipelineEngine` does not need to call `acquire_lock` — that happens at claim time. However, it should:

1. **On pipeline start:** Verify the device lock is held by this job (call `extend_lock` as a liveness check). If verification fails, abort immediately instead of running against an unlocked device.
2. **On pipeline end (success or failure):** Report completion to the backend via the existing `complete_run` endpoint, which will call `release_lock` server-side.

The `LockRenewalManager` already handles periodic renewal during execution — no changes needed to its core loop, only ensure it uses the shared `extend_lock` endpoint.

### D6: Do not add a `session_id` column to JobInstance

The proposal mentioned a possible `session_id` column. After analysis, it's unnecessary: `Device.lock_run_id` already identifies which job holds the session, and `StepTrace.job_id` already links traces to jobs. Adding a separate session concept would create a third identifier without solving a concrete problem.

**Alternative considered:** A dedicated `TestSession` table — rejected as over-engineering. The device lock *is* the session; formalizing it as a separate entity adds complexity without benefit.

## Risks / Trade-offs

**[Risk] Dual-path dispatch increases lock contention under high load**
→ Mitigation: `acquire_lock` uses `FOR UPDATE SKIP LOCKED` semantics — a failing lock attempt is a no-op, not a blocking wait. Jobs stay `PENDING` and are retried on the next poll cycle. No deadlock risk since locks are always acquired on a single row.

**[Risk] Legacy recycler and new watchdog running simultaneously during migration**
→ Mitigation: The watchdog replaces only the host-timeout and device-lock-expiration checks in the recycler. The recycler's other responsibilities (dispatched timeout, running heartbeat timeout for legacy TaskRun, log artifact pruning) remain. Feature flag `USE_SESSION_WATCHDOG` (default `true`) controls whether the new watchdog runs; when enabled, the recycler's overlapping checks are skipped.

**[Risk] Agent claims job but device lock acquire fails on backend (race with another claim)**
→ Mitigation: The claim endpoint wraps state transition + lock acquire in a savepoint. If lock acquire fails, the job stays `PENDING` (transition is rolled back). The Agent receives an empty list and retries next cycle.

**[Risk] 120s host-timeout threshold may be too aggressive for flaky networks**
→ Mitigation: Configurable via env var. The Agent's heartbeat interval is ~5s, so 120s tolerates 24 consecutive failures before triggering — generous enough for intermittent issues.

**[Trade-off] Per-device guard means a host with 3 devices can only run 3 jobs concurrently, even if `max_concurrent_tasks` is higher**
→ Accepted: This is the correct behavior. `max_concurrent_tasks` becomes the upper bound, but physical device count is the real constraint. Document this in the Agent config.

## Migration Plan

1. **Phase 1 — Shared lock service:** Add `backend/services/device_lock.py`. Refactor existing callers (recycler, tasks route, agent_api extend_lock) to use the new functions. No behavior change — pure refactor.
2. **Phase 2 — Workflow dispatch lock:** Integrate `acquire_lock` into `services/dispatcher.py` and `agent_api.py` claim endpoints. Add per-device guard to Agent main loop.
3. **Phase 3 — Watchdog consolidation:** Add `backend/tasks/session_watchdog.py`. Wire it into the app lifespan. Add `USE_SESSION_WATCHDOG` flag; when enabled, disable overlapping recycler checks.
4. **Phase 4 — PipelineEngine hooks:** Add lock verification at pipeline start. Ensure `complete_run` triggers lock release.

Rollback: Each phase is independently deployable. Phase 1 is a pure refactor. Phases 2-4 are gated by `USE_SESSION_WATCHDOG` flag. Rolling back = set flag to `false` and redeploy.

## Open Questions

- Should the UNKNOWN grace period (D4) trigger a re-dispatch attempt before failing? Currently it just transitions to FAILED. A re-dispatch would require resetting the job to PENDING, which could cause infinite retry loops on a truly dead device.
- Should `acquire_lock` support a "queued" response (device locked, but job enqueued for the device) vs a simple boolean? This would enable a future device-level queue, but adds complexity now.
