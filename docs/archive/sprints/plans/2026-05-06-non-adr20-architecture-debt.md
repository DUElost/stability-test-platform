# Non-ADR20 Architecture Debt Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close architecture debts that are not part of ADR-0020: Agent runtime fencing, SAQ blocking, duplicate workflow aggregation, and safe boundaries for later API cleanup.

**Architecture:** Treat fencing token validation as the immediate security boundary. Keep public URLs stable, avoid broad sync-to-async migration, and extract shared pure logic before any larger module split.

**Tech Stack:** FastAPI, SQLAlchemy sync/async sessions, SAQ, pytest, SQLite-backed Agent local cache.

---

### Task 1: Agent StepTrace Fencing

**Files:**
- Modify: `backend/api/routes/agent_api.py`
- Modify: `backend/services/reconciler.py`
- Modify: `backend/agent/registry/local_db.py`
- Modify: `backend/agent/step_trace_uploader.py`
- Modify: `backend/agent/pipeline_engine.py`
- Test: `backend/tests/api/test_agent_dual_write.py`
- Test: `backend/agent/tests/test_fencing_token.py`

- [x] Step 1: Add failing API tests proving `/api/v1/agent/steps` rejects missing/wrong `fencing_token`.
- [x] Step 2: Add failing Agent tests proving locally saved step traces carry `fencing_token`.
- [x] Step 3: Add `fencing_token` to `StepTraceIn`, validate every trace against the active runtime lease, and only then call `reconcile_step_traces`.
- [x] Step 4: Persist `fencing_token` in Agent `step_trace_cache` and include it in StepTrace upload payloads.
- [x] Step 5: Run targeted backend and Agent tests.

### Task 2: Agent Status Endpoint Hardening

**Files:**
- Modify: `backend/api/routes/agent_api.py`
- Test: `backend/tests/api/test_agent_dual_write.py`

- [x] Step 1: Add failing tests proving `/jobs/{job_id}/status` rejects missing/wrong token for non-terminal transitions.
- [x] Step 2: Add `fencing_token` to `JobStatusUpdate` and reuse `_get_valid_runtime_lease` before status transition.
- [x] Step 3: Run targeted route tests.

### Task 3: SAQ Post-Completion Blocking

**Files:**
- Modify: `backend/tasks/saq_tasks.py`
- Test: `backend/tests/tasks/test_saq_tasks.py`

- [x] Step 1: Add failing test proving `post_completion_task` delegates sync report generation via `asyncio.to_thread`.
- [x] Step 2: Wrap `run_post_completion_async(job_id)` in `await asyncio.to_thread(...)`.
- [x] Step 3: Keep exception propagation so SAQ retry behavior remains unchanged.
- [x] Step 4: Run SAQ task tests.

### Task 4: Workflow Aggregation Deduplication

**Files:**
- Create: `backend/services/workflow_aggregation.py`
- Modify: `backend/services/aggregator.py`
- Modify: `backend/services/aggregator_sync.py`
- Test: `backend/tests/services/test_workflow_aggregation_shared.py`

- [x] Step 1: Add failing tests for shared aggregation and async/sync delegation.
- [x] Step 2: Implement a pure `apply_workflow_aggregation(run, jobs)` helper.
- [x] Step 3: Replace duplicated status math in async and sync aggregators with the helper.
- [x] Step 4: Run aggregation and aggregator-adjacent tests.

### Task 5: P2 Boundary Record

**Files:**
- Create: `docs/architecture/non-adr20-followups.md`

- [x] Step 1: Record route-split target modules and API envelope migration order.
- [x] Step 2: Explicitly mark P2 as deferred until ADR-0020 code migration stabilizes.
- [x] Step 3: Include grep commands that identify remaining old envelope routes.

### Verification

- [x] Run targeted backend tests for Agent API fencing.
- [x] Run targeted Agent tests for local cache and uploader.
- [x] Run targeted SAQ tests.
- [x] Run workflow aggregation tests.
- [x] Run `rg` checks for new `fencing_token` propagation and old unsafe step upload paths.
