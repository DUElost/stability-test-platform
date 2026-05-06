# Non-ADR20 Followups

This note tracks architecture cleanup intentionally deferred from the current
non-ADR20 fix batch.

## Route Split Boundary

`backend/api/routes/agent_api.py` should be split only after ADR-0020 migration
stabilizes. Keep URL compatibility and move handlers by runtime concern:

- `agent_claims.py`: claim, pending compatibility, recovery sync.
- `agent_runtime.py`: heartbeat, complete, extend lock, job status, step status.
- `agent_ingest.py`: batch step traces and log signals.
- `agent_control.py`: backpressure and control-plane endpoints.

Before splitting, extract shared schemas and helpers into a local package such
as `backend/api/routes/agent/` so route modules do not import each other.

## Response Envelope Order

Do not convert all responses in one pass. Migrate by external surface:

1. Agent runtime endpoints, because Agent retry logic depends on status codes.
2. Admin/user-facing workflow APIs.
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

P2 is deferred until ADR-0020 code migration stabilizes because route splitting
and envelope migration will otherwise create unnecessary merge churn.
