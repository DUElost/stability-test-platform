# Non-ADR20 Followups

This note tracks architecture cleanup that is still intentionally separate from
the ADR-0020 Plan/PlanStep migration. It is an active debt note, not an
implementation spec.

## Route Split Boundary

**Tracking:** [#60](https://github.com/DUElost/stability-test-platform/issues/60)
（tech-debt：建议拆分、非目标、择机时机与验收标准）。

**Status (2026-07-17 verified):** `backend/api/routes/agent_api.py` is still a
single broad module (~2420 lines, 15 HTTP handlers). No `agent_claims.py` /
`agent_runtime.py` / `agent_ingest.py` / `agent_control.py` split exists yet.
URL paths under `/agent/...` remain unchanged; this debt is about module
ownership only.

Keep URL compatibility and move handlers by runtime concern when this debt is
picked up:

| Target module | Current handlers (path → function) |
|---|---|
| `agent_claims.py` | `POST /jobs/claim` → `claim_jobs`; `GET /jobs/pending` → `get_pending_jobs`; `POST /recovery/sync` → `recovery_sync` |
| `agent_runtime.py` | `POST /heartbeat` → `agent_heartbeat`; `POST /jobs/{id}/heartbeat` → `job_heartbeat`; `POST /jobs/{id}/complete` → `complete_job`; `POST /jobs/{id}/status` → `update_job_status`; `POST /jobs/{id}/extend_lock` → `extend_job_lock`; `POST /leases/extend-batch` → `extend_leases_batch`; `POST /jobs/{id}/steps/{step_id}/status` → `update_job_step_status`; `POST /jobs/{id}/patrol-heartbeat` → `patrol_heartbeat` |
| `agent_ingest.py` | `POST /steps` → `upload_step_traces`; `POST /log-signals` → `ingest_log_signals`; `POST /jobs/{id}/artifacts` → `ingest_artifact` |
| `agent_control.py` | `GET /{host_id}/archive-status` → `get_archive_status` (+ any future backpressure / control-plane-only endpoints) |

Before splitting, extract shared schemas and helpers into a local package such
as `backend/api/routes/agent/` so route modules do not import each other.

## Response Envelope Order

Do not convert all responses in one pass. Migrate by external surface:

1. Agent runtime endpoints, because Agent retry logic depends on status codes.
2. Admin/user-facing execution APIs.
3. Deprecated compatibility routes, with explicit deprecation headers.

Each migration step should include a contract test for both success and error
shape.

## Grep Checks

```bash
rg -n 'response_model=.*ApiResponse|return ok\(|return err\(' backend/api/routes
rg -n 'HTTPException\(|detail=\{|detail=\[' backend/api/routes
rg -n '@router\.(get|post|put|patch|delete)' backend/api/routes/agent_api.py
```

P2 remains deferred until route ownership and external contract tests are ready
to move together without creating unnecessary merge churn.
