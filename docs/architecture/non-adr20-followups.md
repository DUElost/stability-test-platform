# Non-ADR20 Followups

This note tracks architecture cleanup that is still intentionally separate from
the ADR-0020 Plan/PlanStep migration. It is an active debt note, not an
implementation spec.

## Route Split Boundary

`backend/api/routes/agent_api.py` is still a broad route module. Keep URL
compatibility and move handlers by runtime concern when this debt is picked up:

- `agent_claims.py`: claim, pending compatibility, recovery sync.
- `agent_runtime.py`: heartbeat, complete, extend lock, job status, step status.
- `agent_ingest.py`: batch step traces and log signals.
- `agent_control.py`: backpressure and control-plane endpoints.

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

Use these commands to find remaining boundary debt:

```powershell
rg -n "response_model=.*ApiResponse|return ok\\(|return err\\(" backend/api/routes
rg -n "HTTPException\\(|detail=\\{|detail=\\[" backend/api/routes
rg -n "@router\\.(get|post|put|patch|delete)" backend/api/routes/agent_api.py
```

P2 remains deferred until route ownership and external contract tests are ready
to move together without creating unnecessary merge churn.
