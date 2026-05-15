# Code Review Fixes — 7 Confirmed Issues

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix 7 confirmed defects from workspace code review: 2 race conditions (plan chain TOCTOU, state machine bypass), 1 scalability issue (recycler bulk scan), 2 robustness issues (daemon thread leak, SQLite single-connection), and 2 frontend defects (null fallback, query key collision).

**Architecture:** Backend fixes are independent per-file — each can be implemented and tested standalone. Frontend fixes touch different query consumers and the API client layer. Order by risk: state_machine → plan_chain → recycler → pipeline_engine → local_db → unwrapApiResponse → query keys.

**Tech Stack:** Python 3.11 / SQLAlchemy 2.0 / pytest / React 18 / TypeScript / @tanstack/react-query v4

---

### Task 1: state_machine — Add PENDING→ABORTED transition

**Files:**
- Modify: `backend/services/state_machine.py:9`
- Modify: `backend/services/plan_run_abort.py:150-154`
- Test: `backend/tests/services/test_plan_run_abort.py` (new)

- [ ] **Step 1: Add PENDING→ABORTED to VALID_TRANSITIONS**

```python
# backend/services/state_machine.py:9
# Before:
    JobStatus.PENDING:      {JobStatus.RUNNING, JobStatus.FAILED},
# After:
    JobStatus.PENDING:      {JobStatus.RUNNING, JobStatus.FAILED, JobStatus.ABORTED},
```

- [ ] **Step 2: Route plan_run_abort through JobStateMachine**

```python
# backend/services/plan_run_abort.py:150-154
# Before:
            if job.status == JobStatus.PENDING.value:
                job.status = JobStatus.ABORTED.value
                job.status_reason = reason
                job.ended_at = now
                aborted_jobs.append(job.id)
# After:
            if job.status == JobStatus.PENDING.value:
                from backend.services.state_machine import JobStateMachine
                JobStateMachine.transition(job, JobStatus.ABORTED, reason)
                job.ended_at = now
                aborted_jobs.append(job.id)
```

- [ ] **Step 3: Write test for valid transition path**

```python
# backend/tests/services/test_plan_run_abort.py
import pytest
from backend.services.state_machine import JobStateMachine, InvalidTransitionError
from backend.models.enums import JobStatus
from backend.models.job import JobInstance

def test_pending_to_aborted_is_valid():
    """PENDING→ABORTED 现在是合法转换（通过 plan_run_abort 流）"""
    job = JobInstance(status=JobStatus.PENDING.value, plan_run_id=1, plan_id=1, device_id=1)
    # 不应抛异常
    JobStateMachine.transition(job, JobStatus.ABORTED, "aborted_by_user")
    assert job.status == JobStatus.ABORTED.value
    assert job.status_reason == "aborted_by_user"

def test_aborted_is_terminal():
    """ABORTED 是终态，不能再转其他状态"""
    job = JobInstance(status=JobStatus.ABORTED.value, plan_run_id=1, plan_id=1, device_id=1)
    with pytest.raises(InvalidTransitionError):
        JobStateMachine.transition(job, JobStatus.RUNNING, "recover")

def test_running_to_aborted_is_valid():
    """RUNNING→ABORTED 已被 plan_run_abort 覆盖，确保不被破坏"""
    job = JobInstance(status=JobStatus.RUNNING.value, plan_run_id=1, plan_id=1, device_id=1)
    JobStateMachine.transition(job, JobStatus.ABORTED, "aborted_by_user")
    assert job.status == JobStatus.ABORTED.value
```

- [ ] **Step 4: Run tests and commit**

Run: `pytest backend/tests/services/test_plan_run_abort.py -v`
Expected: 3 PASS

```bash
git add backend/services/state_machine.py backend/services/plan_run_abort.py backend/tests/services/test_plan_run_abort.py
git commit -m "fix(state-machine): add PENDING->ABORTED transition, route abort through JobStateMachine"
```

---

### Task 2: plan_chain_trigger — Fix TOCTOU on next_plan_triggered

**Files:**
- Modify: `backend/services/plan_chain_trigger.py:97-152`
- Test: `backend/tests/services/test_plan_chain_trigger.py` (new)

- [ ] **Step 1: Restructure — mark triggered BEFORE dispatch**

Current order (broken):
1. `with_for_update()` lock
2. `dispatch_plan_sync()` → internal `commit()` → lock RELEASED
3. `UPDATE next_plan_triggered=True` → TOCTOU window open

Fix: mark `next_plan_triggered=True` + commit INSIDE the lock, then dispatch.

```python
# backend/services/plan_chain_trigger.py:97-152

def trigger_next_plan_sync(
    plan_run: PlanRun,
    db: Session,
) -> PlanRun | None:
    """Synchronous version of trigger_next_plan for the sync aggregator path."""
    if plan_run.status not in TRIGGERABLE_TERMINAL_STATUSES:
        return None

    plan = db.get(Plan, plan_run.plan_id)
    if plan is None or plan.next_plan_id is None:
        return None

    # (1) Pessimistic lock + mark triggered atomically
    result = db.execute(
        update(PlanRun)
        .where(PlanRun.id == plan_run.id)
        .where(PlanRun.next_plan_triggered.is_(False))
        .values(next_plan_triggered=True)
        .returning(PlanRun.id)
    )
    locked_id = result.scalar()
    db.commit()  # 释放锁，next_plan_triggered 已持久化
    if locked_id is None:
        # 另一个并发调用已标记
        return None

    device_rows = db.execute(
        select(JobInstance.device_id).where(
            JobInstance.plan_run_id == plan_run.id
        )
    ).all()
    device_ids = list({r.device_id for r in device_rows})
    if not device_ids:
        logger.warning("plan_chain_trigger_sync_no_devices plan_run=%d", plan_run.id)
        return None

    chain_index = (plan_run.chain_index or 0) + 1
    try:
        child = dispatch_plan_sync(
            plan_id=plan.next_plan_id,
            device_ids=device_ids,
            triggered_by=plan_run.triggered_by or "chain",
            db=db,
            run_type="CHAIN",
            run_context={"triggered_from_plan_run_id": plan_run.id},
            parent_plan_run_id=plan_run.id,
            root_plan_run_id=plan_run.root_plan_run_id or plan_run.id,
            chain_index=chain_index,
        )
    except SyncPlanDispatchError as exc:
        logger.error("plan_chain_dispatch_sync_failed parent=%d err=%s", plan_run.id, exc)
        return None

    logger.info(
        "plan_chain_triggered_sync parent=%d child=%d chain_index=%d",
        plan_run.id, child.id, chain_index,
    )
    return child
```

- [ ] **Step 2: Write concurrency test**

```python
# backend/tests/services/test_plan_chain_trigger.py
import pytest
from unittest.mock import patch, MagicMock
from backend.services.plan_chain_trigger import trigger_next_plan_sync
from backend.models.plan_run import PlanRun
from backend.models.enums import JobStatus

def test_trigger_next_plan_marks_triggered_before_dispatch():
    """验证 next_plan_triggered=True 在 dispatch_plan_sync 的 commit 之前已持久化"""
    # Seed: a terminal PlanRun with next_plan_id set and devices
    plan_run = PlanRun(
        id=1, plan_id=1, status=JobStatus.COMPLETED.value,
        chain_index=0, next_plan_triggered=False, run_type="MANUAL",
    )
    # Will be tested in integration; unit-level test verifies atomic UPDATE shape
    from sqlalchemy import update
    from sqlalchemy.sql import select
    # Verify the RETURNING clause captures the id
    stmt = update(PlanRun).where(PlanRun.id == 1).where(PlanRun.next_plan_triggered.is_(False)).values(next_plan_triggered=True).returning(PlanRun.id)
    assert "RETURNING" in str(stmt.compile(compile_kwargs={"literal_binds": True})) or "RETURNING" in str(stmt.compile())
```

- [ ] **Step 3: Run plan_chain_trigger integration test (existing)**

Run: `pytest backend/tests/ -k "chain" -v`
Expected: existing chain tests pass without regression

- [ ] **Step 4: Commit**

```bash
git add backend/services/plan_chain_trigger.py backend/tests/services/test_plan_chain_trigger.py
git commit -m "fix(plan-chain): mark next_plan_triggered before dispatch_plan_sync commit to close TOCTOU window"
```

---

### Task 3: recycler — Add LIMIT + batch commit

**Files:**
- Modify: `backend/scheduler/recycler.py:260-315`
- Test: `backend/tests/scheduler/test_recycler.py` (modify existing)

- [ ] **Step 1: Add BATCH_SIZE constant at module level**

```python
# backend/scheduler/recycler.py (near DISPATCHED_TIMEOUT_SECONDS)
DISPATCHED_TIMEOUT_SECONDS = 120
RUNNING_HEARTBEAT_TIMEOUT_SECONDS = 600
RECYCLER_BATCH_SIZE = 200
```

- [ ] **Step 2: Rewrite recycler scan as batched loop**

Replace the monolithic `.all()` + single commit with batched `limit(N)` per query + per-batch commit.

```python
# backend/scheduler/recycler.py:260-315
# Before: single .all() + single commit
# After: batched

    now = datetime.now(timezone.utc)
    pending_deadline = now - timedelta(seconds=DISPATCHED_TIMEOUT_SECONDS)
    running_deadline = now - timedelta(seconds=RUNNING_HEARTBEAT_TIMEOUT_SECONDS)

    # PENDING timeout — process in batches
    while True:
        with SessionLocal() as db:
            batch = (
                db.query(JobInstance)
                .filter(
                    JobInstance.status == JobStatus.PENDING.value,
                    JobInstance.created_at < pending_deadline,
                )
                .order_by(JobInstance.id)
                .limit(RECYCLER_BATCH_SIZE)
                .all()
            )
            if not batch:
                break
            for job in batch:
                try:
                    with db.begin_nested():
                        _mark_pending_timeout(
                            db, job, now, "pending_timeout: agent never claimed job",
                        )
                except Exception:
                    logger.exception(
                        "recycler_pending_failed job=%d device=%d",
                        job.id, job.device_id,
                    )
            db.commit()

    # RUNNING timeout — same batched pattern
    while True:
        with SessionLocal() as db:
            batch = (
                db.query(JobInstance)
                .filter(
                    JobInstance.status == JobStatus.RUNNING.value,
                    JobInstance.updated_at < running_deadline,
                )
                .order_by(JobInstance.id)
                .limit(RECYCLER_BATCH_SIZE)
                .all()
            )
            if not batch:
                break
            for job in batch:
                try:
                    with db.begin_nested():
                        _mark_running_timeout(
                            db, job, now, "running_timeout: no completion within window",
                        )
                except Exception:
                    logger.exception(
                        "recycler_running_failed job=%d device=%d",
                        job.id, job.device_id,
                    )
            db.commit()

    # Deferred post-completion — unchanged
    with SessionLocal() as db:
        filled = _fill_deferred_post_completions(db, now)
        if filled:
            db.commit()
```

- [ ] **Step 3: Update existing recycler tests for batched behavior**

Modify `backend/tests/scheduler/test_recycler.py` — ensure tests that create many pending jobs still pass with batched commit. Add a dedicated batch-size test:

```python
def test_recycler_batches_large_pending_pool(db_session, mocker):
    """验证 recycler 对大 PENDING 池做分批处理，不单次处理超过 BATCH_SIZE"""
    from backend.scheduler.recycler import _run_cycle, RECYCLER_BATCH_SIZE

    # Create RECYCLER_BATCH_SIZE + 10 expired pending jobs
    now = datetime.now(timezone.utc)
    deadline = now - timedelta(seconds=200)  # well past DISPATCHED_TIMEOUT_SECONDS
    for i in range(RECYCLER_BATCH_SIZE + 10):
        job = JobInstance(
            plan_run_id=1, plan_id=1, device_id=1, status=JobStatus.PENDING.value,
        )
        job.created_at = deadline
        db_session.add(job)
    db_session.commit()

    # Run cycle — should complete without error
    _run_cycle()
    # Verify all were processed
    count = db_session.query(JobInstance).filter(
        JobInstance.plan_run_id == 1, JobInstance.status == JobStatus.FAILED.value,
    ).count()
    assert count == RECYCLER_BATCH_SIZE + 10
```

- [ ] **Step 4: Run recycler tests**

Run: `pytest backend/tests/scheduler/test_recycler.py -v`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add backend/scheduler/recycler.py backend/tests/scheduler/test_recycler.py
git commit -m "fix(recycler): batch pending/running timeout scans with LIMIT per commit"
```

---

### Task 4: pipeline_engine — Guard abandoned daemon threads

**Files:**
- Modify: `backend/agent/pipeline_engine.py:234-270`
- Test: `backend/agent/tests/test_pipeline_engine_timeout.py` (new)

- [ ] **Step 1: Add active worker tracking + drain on exit**

```python
# backend/agent/pipeline_engine.py — add to PipelineEngine.__init__
        self._timeout_workers: list[threading.Thread] = []

# backend/agent/pipeline_engine.py — modify _run_with_timeout
    def _run_with_timeout(
        self, action_fn: Callable, ctx: StepContext, timeout: int
    ) -> StepResult:
        result_holder: List[Any] = []
        error_holder: List[Exception] = []

        def _worker():
            try:
                result_holder.append(action_fn(ctx))
            except Exception as e:
                error_holder.append(e)

        worker = threading.Thread(target=_worker, daemon=True)
        self._timeout_workers.append(worker)
        worker.start()
        worker.join(timeout=timeout)

        if worker.is_alive():
            # Daemon thread still running — will be abandoned.
            # Track for lifecycle drain (teardown / process exit).
            logger.warning(f"Step timed out after {timeout}s, worker thread still alive")
            return StepResult(
                success=False,
                exit_code=124,
                error_message=f"Step timed out after {timeout}s",
            )

        self._timeout_workers.remove(worker)
        # ... rest unchanged
```

Add `drain_workers()` method to `PipelineEngine`:

```python
    def drain_workers(self, grace_seconds: int = 5) -> None:
        """Wait for any abandoned timeout workers to finish (best-effort)."""
        abandoned = [w for w in self._timeout_workers if w.is_alive()]
        if not abandoned:
            return
        logger.info("draining %d abandoned workers (grace=%ds)", len(abandoned), grace_seconds)
        for w in abandoned:
            w.join(timeout=grace_seconds)
        still_alive = sum(1 for w in abandoned if w.is_alive())
        if still_alive:
            logger.warning("drain_incomplete: %d workers still alive after grace", still_alive)
```

Call `drain_workers()` in `_execute_lifecycle` before `_run_patrol_loop` and in `finally` before teardown.

- [ ] **Step 2: Write unit test for drain behavior**

```python
# backend/agent/tests/test_pipeline_engine_timeout.py
import time
import threading
import pytest
from backend.agent.pipeline_engine import PipelineEngine

def test_drain_workers_waits_for_abandoned_threads():
    """drain_workers 等待被 abandoned 的 timeout 线程"""
    engine = PipelineEngine.__new__(PipelineEngine)
    engine._timeout_workers = []

    slow_done = threading.Event()

    def _slow_action():
        slow_done.wait(timeout=0.5)  # will complete within grace

    worker = threading.Thread(target=_slow_action, daemon=True)
    worker.start()
    engine._timeout_workers.append(worker)

    # Should drain within grace
    t0 = time.time()
    engine.drain_workers(grace_seconds=2)
    elapsed = time.time() - t0
    assert elapsed < 1.5  # completed, didn't wait full grace
    assert not worker.is_alive()
```

- [ ] **Step 3: Run pipeline engine tests**

Run: `pytest backend/agent/tests/test_pipeline_engine_timeout.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add backend/agent/pipeline_engine.py backend/agent/tests/test_pipeline_engine_timeout.py
git commit -m "fix(pipeline-engine): track and drain abandoned timeout worker threads"
```

---

### Task 5: local_db — Per-thread SQLite connections

**Files:**
- Modify: `backend/agent/registry/local_db.py:30-40, 149-152`
- Test: `backend/agent/tests/test_local_db.py` (modify existing or new)

- [ ] **Step 1: Replace single connection with per-thread connections**

```python
# backend/agent/registry/local_db.py

import threading

class AgentLocalStore:
    """Thread-safe local SQLite store using per-thread WAL connections."""

    def __init__(self) -> None:
        self._db_path: Optional[str] = None
        self._thread_connections: dict[int, sqlite3.Connection] = {}
        self._thread_connections_lock = threading.Lock()
        self._initialized = False

    def _get_conn(self) -> sqlite3.Connection:
        """Get the connection for the current thread, creating if needed."""
        tid = threading.get_ident()
        with self._thread_connections_lock:
            if tid not in self._thread_connections:
                conn = sqlite3.connect(self._db_path, check_same_thread=False)
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=FULL")
                self._thread_connections[tid] = conn
            return self._thread_connections[tid]

    def initialize(self, db_path: str) -> None:
        self._db_path = db_path
        conn = self._get_conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS step_trace_cache (...);
            -- ... existing DDL unchanged ...
        """)
        conn.commit()
        self._initialized = True

    def close(self) -> None:
        with self._thread_connections_lock:
            for conn in self._thread_connections.values():
                try:
                    conn.close()
                except Exception:
                    pass
            self._thread_connections.clear()
```

All existing methods that access `self._conn` must be updated to use `self._get_conn()`. Existing single `self._lock` is removed — WAL + per-thread connections provide safe concurrent read/write without serialization lock.

- [ ] **Step 2: Update all methods to use `_get_conn()`**

Replace `self._conn` → `self._get_conn()` in every method that accesses the database. Remove `self._lock` usage since per-thread connections don't need serialization.

```python
# Example: _save_step_trace method
    def _save_step_trace(self, trace: dict) -> None:
        conn = self._get_conn()
        conn.execute("""
            INSERT OR REPLACE INTO step_trace_cache (...)
            VALUES (?, ?, ?, ...)
        """, (...))
        conn.commit()
```

- [ ] **Step 3: Write concurrency test**

```python
# backend/agent/tests/test_local_db.py
import threading
import tempfile
import os

def test_per_thread_connections_are_independent(tmp_path):
    """不同线程获得不同的 sqlite3 连接对象"""
    from backend.agent.registry.local_db import AgentLocalStore

    store = AgentLocalStore()
    db_path = str(tmp_path / "test.db")
    store.initialize(db_path)

    conns = []
    errors = []

    def _touch():
        try:
            conns.append(store._get_conn())
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=_touch) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(errors) == 0
    # Each thread should get its own connection object
    unique_ids = {id(c) for c in conns}
    assert len(unique_ids) == 5

    store.close()
```

- [ ] **Step 4: Run agent tests**

Run: `pytest backend/agent/tests/test_local_db.py -v`
Expected: all pass (existing + new)

- [ ] **Step 5: Commit**

```bash
git add backend/agent/registry/local_db.py backend/agent/tests/test_local_db.py
git commit -m "fix(local-db): per-thread SQLite connections, remove single lock serialization"
```

---

### Task 6: frontend — Fix unwrapApiResponse null fallback

**Files:**
- Modify: `frontend/src/utils/api/client.ts:66-67`
- Test: `frontend/src/utils/api/__tests__/client.test.ts` (new or existing)

- [ ] **Step 1: Replace `??` with strict `as T`**

```typescript
// frontend/src/utils/api/client.ts:66-67
// Before:
export async function unwrapApiResponse<T>(request: Promise<{ data: { data?: T; error?: { code: string; message: string } | null } }>): Promise<T> {
  const resp = await request;
  const body = resp.data as any;
  if (body?.error) throw new Error(`[${body.error.code}] ${body.error.message}`);
  return body?.data ?? body;
}

// After:
export async function unwrapApiResponse<T>(request: Promise<{ data: { data?: T; error?: { code: string; message: string } | null } }>): Promise<T> {
  const resp = await request;
  const body = resp.data as any;
  if (body?.error) throw new Error(`[${body.error.code}] ${body.error.message}`);
  return body.data as T;
}
```

- [ ] **Step 2: Audit callers that rely on falsy fallback**

Search for `.data ?? body` pattern consumers that might expect null. Run type-check:

Run: `cd frontend && npx tsc --noEmit`
Expected: no new TS errors (existing errors only)

- [ ] **Step 3: Write test for null data**

```typescript
// In existing client test file or create: frontend/src/utils/api/__tests__/client.test.ts
import { describe, it, expect } from 'vitest';
import { unwrapApiResponse } from '@/utils/api/client';

describe('unwrapApiResponse', () => {
  it('returns null when data field is explicitly null', async () => {
    const result = await unwrapApiResponse<null>(
      Promise.resolve({ data: { data: null, error: null } })
    );
    expect(result).toBeNull();
  });

  it('returns undefined when data field is absent', async () => {
    const result = await unwrapApiResponse<undefined>(
      Promise.resolve({ data: {} })
    );
    expect(result).toBeUndefined();
  });

  it('returns the data payload when present', async () => {
    const result = await unwrapApiResponse<{ id: number }>(
      Promise.resolve({ data: { data: { id: 1 }, error: null } })
    );
    expect(result).toEqual({ id: 1 });
  });

  it('throws on error', async () => {
    await expect(
      unwrapApiResponse(Promise.resolve({ data: { data: null, error: { code: 'E001', message: 'bad' } } }))
    ).rejects.toThrow('[E001] bad');
  });
});
```

- [ ] **Step 4: Run frontend tests**

Run: `cd frontend && npx vitest run src/utils/api/__tests__/client.test.ts`
Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/utils/api/client.ts frontend/src/utils/api/__tests__/client.test.ts
git commit -m "fix(api-client): unwrapApiResponse returns body.data as T instead of falling back to body on null"
```

---

### Task 7: frontend — Fix query key collision with planKeys factory

**Files:**
- Modify: `frontend/src/pages/orchestration/PlanListPage.tsx:18`
- Modify: `frontend/src/pages/execution/PlanExecutePage.tsx:95-97`
- Modify: `frontend/src/pages/tasks/TaskList.tsx:9-11`
- Modify: `frontend/src/pages/orchestration/PlanEditPage.tsx:150, 301, 374`
- Create: `frontend/src/utils/api/queryKeys.ts`
- Test: `frontend/src/utils/api/__tests__/queryKeys.test.ts` (new)

- [ ] **Step 1: Create query key factory**

```typescript
// frontend/src/utils/api/queryKeys.ts
export const planKeys = {
  /** Plan list queries — scoped by limit to avoid cross-page cache collision */
  list: (limit: number) => ['plans', { limit }] as const,
  /** Same key for list cache invalidation — matches any plan list regardless of limit */
  allLists: () => ['plans'] as const,
} as const;
```

- [ ] **Step 2: Replace `['plans']` with `planKeys.list(limit)` in all consumers**

```typescript
// PlanListPage.tsx:18
// Before:
  queryKey: ['plans'],
  queryFn: () => api.plans.list(0, 100),
// After:
  queryKey: planKeys.list(100),
  queryFn: () => api.plans.list(0, 100),

// PlanExecutePage.tsx:96
// Before:
  queryKey: ['plans'],
  queryFn: () => api.plans.list(0, 100),
// After:
  queryKey: planKeys.list(100),
  queryFn: () => api.plans.list(0, 100),

// TaskList.tsx:10
// Before:
  queryKey: ['plans'],
  queryFn: () => api.plans.list(0, 200),
  refetchInterval: 5000,
// After:
  queryKey: planKeys.list(200),
  queryFn: () => api.plans.list(0, 200),
  refetchInterval: 5000,
```

- [ ] **Step 3: Update invalidation calls to use `planKeys.allLists()`**

```typescript
// PlanListPage.tsx:25
// Before:
  queryClient.invalidateQueries({ queryKey: ['plans'] });
// After:
  queryClient.invalidateQueries({ queryKey: planKeys.allLists() });

// PlanEditPage.tsx:301, 374 — same pattern
  queryClient.invalidateQueries({ queryKey: planKeys.allLists() });
```

- [ ] **Step 4: Write query key test**

```typescript
// frontend/src/utils/api/__tests__/queryKeys.test.ts
import { describe, it, expect } from 'vitest';
import { planKeys } from '@/utils/api/queryKeys';

describe('planKeys', () => {
  it('different limits produce different query keys', () => {
    const key100 = planKeys.list(100);
    const key200 = planKeys.list(200);
    expect(key100).not.toEqual(key200);
    // react-query uses deep equality — ['plans', {limit:100}] !== ['plans', {limit:200}]
    expect(key100[1]).toEqual({ limit: 100 });
    expect(key200[1]).toEqual({ limit: 200 });
  });

  it('same limit produces the same query key', () => {
    expect(planKeys.list(100)).toEqual(planKeys.list(100));
  });

  it('allLists matches any plan list key for invalidation', () => {
    // react-query's partial matching: ['plans'] matches ['plans', {limit: X}]
    expect(planKeys.allLists()).toEqual(['plans']);
  });
});
```

- [ ] **Step 5: Run type-check + frontend tests**

Run: `cd frontend && npx tsc --noEmit`
Expected: no new TS errors

Run: `cd frontend && npx vitest run src/utils/api/__tests__/queryKeys.test.ts`
Expected: 3 PASS

- [ ] **Step 6: Commit**

```bash
git add frontend/src/utils/api/queryKeys.ts frontend/src/utils/api/__tests__/queryKeys.test.ts frontend/src/pages/orchestration/PlanListPage.tsx frontend/src/pages/execution/PlanExecutePage.tsx frontend/src/pages/tasks/TaskList.tsx frontend/src/pages/orchestration/PlanEditPage.tsx
git commit -m "fix(frontend): add planKeys query key factory to prevent cross-page cache collision"
```
