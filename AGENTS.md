# AGENTS.md

## Dev commands

| What | Command |
|------|---------|
| Backend | `uvicorn backend.main:app --host 0.0.0.0 --port 8000` (add `--reload` for dev) |
| Frontend | `cd frontend && npm run dev` (also: `npm run type-check`, `npm run build`) |
| Backend tests | `pytest backend/tests/` |
| Agent tests | `pytest backend/agent/tests/` |
| Frontend tests | `cd frontend && npx vitest run` |
| TypeScript | `cd frontend && npx tsc --noEmit` |
| Migrations | `cd backend && python -m alembic upgrade head` |
| Agent (dev) | `python -m backend.agent.main` (set `API_URL` env first) |

**start-backend.bat** runs `alembic upgrade head` then uvicorn. Set `STP_BACKEND_RELOAD=1` for hot reload (default off — real device safety).

## Test quirks

- Backend pytest needs `TEST_DATABASE_URL` (PostgreSQL). Set `ALLOW_SQLITE_TESTS=1` for local SQLite (no PG required).
- `os.environ["TESTING"] = "1"` is set in `backend/tests/conftest.py` — this disables Redis/SAQ/APScheduler startup in lifespan.
- Frontend tests use vitest + jsdom, `@/` path alias maps to `src/`.
- Two separate test dirs: `backend/tests/` and `backend/agent/tests/` — run separately.

## Architecture

- **app** = `socketio.ASGIApp(sio_server, fastapi_app)` — combined ASGI mount in `backend/main.py:122`.
- **Frontend pages** are `React.lazy()` loaded via `frontend/src/router/index.tsx`.
- **API client** modules in `frontend/src/utils/api/` (`planRuns.ts`, `hosts.ts`, `plans.ts`, etc.).
- **ADR-0020**: replaced Workflow/TaskTemplate → Plan/PlanStep, WorkflowRun → PlanRun. No `plan.lifecycle` column — lifecycle is composed from `PlanStep` rows + `patrol_interval_seconds`/`timeout_seconds` at dispatch time.
- **ADR-0018**: Watcher subsystem default-on via `STP_WATCHER_ENABLED=true` (or `STP_WATCHER_PLAN_DEFAULT=true`). Set both to `false` to disable entirely.
- **ADR-0025**: Log archiver runs independently of Watcher; `STP_LOG_ARCHIVE_NFS_BASE_DIR` overrides NFS root for archives (falls back to watcher/AEE NFS env).
- **Agent** runs on Linux hosts, connects Android devices via ADB. Two enrollment paths: `install_agent.sh` (systemd) or dev `python -m backend.agent.main`.

## Key conventions

- Only `script:<name>` action type is supported in pipeline_def (see `CLAUDE.md` §Pipeline).
- Script `default_params` are immutable after creation — `PUT` returns 422. New version via `POST /api/v1/scripts/{name}/versions`.
- `STP_SCRIPT_ROOT` must be explicitly set in dev (e.g., `<repo>/backend/agent/scripts`).
- DB table names are singular (`device`, `host`, `plan`).
- WSL Agent needs `ANDROID_ADB_SERVER_PORT=5039`.
- Production Agent needs `AGENT_SECRET` env for SocketIO auth.
- `start-backend.bat` checks port conflict before starting.
- `frontend/src/utils/api/types.ts` is the canonical type source — keep in sync with backend Pydantic schemas.

## CI pipeline (`.github/workflows/ci.yml`)

Backend: `compileall backend/` → `pytest backend/tests/` (PostgreSQL service). Frontend: `tsc --noEmit` → `npm run build`. Docker build after both pass.
