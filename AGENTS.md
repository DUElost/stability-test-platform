# AGENTS.md

## Dev commands

| What | Command |
|------|---------|
| Backend | `uvicorn backend.main:app --host 0.0.0.0 --port 8000` (add `--reload` for dev) |
| Frontend | `cd frontend && npm run dev` (also: `npm run type-check`, `npm run build`) |
| Backend tests | `pytest backend/tests/` (needs PostgreSQL or `ALLOW_SQLITE_TESTS=1`) |
| Agent tests | `pytest backend/agent/tests/` (no PG required, runs fast) |
| Frontend tests | `cd frontend && npx vitest run` (or `npx vitest run path/to/test.tsx` for single file) |
| TypeScript | `cd frontend && npx tsc --noEmit` |
| Migrations | `cd backend && python -m alembic upgrade head` |
| Agent (dev) | `python -m backend.agent.main` (set `API_URL` env first) |

**start-backend.bat** runs `alembic upgrade head` then uvicorn. Set `STP_BACKEND_RELOAD=1` for hot reload (default off — real device safety).

**Verification order**: agent tests → tsc → build → (backend tests if PG available).

## Test quirks

- Backend pytest needs `TEST_DATABASE_URL` (PostgreSQL). Set `ALLOW_SQLITE_TESTS=1` for local SQLite (no PG required, but `test_agent_dual_write.py` skips on SQLite — needs PG partial unique index).
- `os.environ["TESTING"] = "1"` is set in `backend/tests/conftest.py` — this disables Redis/SAQ/APScheduler startup in lifespan.
- Backend full-suite can timeout locally due to session-scoped engine fixture. Run single files: `pytest backend/tests/api/test_dedup_scan_endpoints.py -x`.
- Agent tests (`backend/agent/tests/`) are self-contained — no DB/Redis, fast (~30s for 600 tests). Control-plane tests that need DB go in `backend/tests/`, not `backend/agent/tests/`.
- Frontend tests use vitest + jsdom, `@/` path alias maps to `src/`.
- WATCHER_SIGNAL invalidation is debounced 2s in `PlanRunDetailPage.tsx` — tests asserting refetch need `waitFor({ timeout: 4000 })`.

## Architecture

- **app** = `socketio.ASGIApp(sio_server, fastapi_app)` — combined ASGI mount in `backend/main.py:122`.
- **Frontend pages** are `React.lazy()` loaded via `frontend/src/router/index.tsx`.
- **API client** modules in `frontend/src/utils/api/` (`planRuns.ts`, `hosts.ts`, `plans.ts`, etc.).
- **ADR-0020**: Plan/PlanStep replaced Workflow/TaskTemplate. No `plan.lifecycle` column — lifecycle composed from `PlanStep` rows + `patrol_interval_seconds`/`timeout_seconds` at dispatch time.
- **ADR-0018**: Watcher subsystem gated by `STP_WATCHER_ENABLED` (default `true`). Agent inotifyd monitors device AEE/ANR directories → `job_log_signal` table → frontend `watcher-summary`.
- **ADR-0025**: Plan C — Agent local scan + on-demand upload + control-plane merge. Scan tool is `start_log_scan.py` (external, deployed on 15.4 CIFS share). Three-phase archive: SSD→HDD→15.4 CIFS.
- **Agent** runs on Linux hosts, connects Android devices via ADB. Two enrollment paths: `install_agent.sh` (systemd) or dev `python -m backend.agent.main`.
- **SAQ pipeline** (Sprint 4): `scan_task` → `upload_task` → `merge_task` chain. `scan_task` polls NFS for all host artifacts before enqueuing follow-ups.

## AEE crash detection chain (初筛选)

1. Agent Watcher `inotifyd` detects AEE file on device → `SignalEmitter` → local SQLite outbox → `POST /agent/log-signals` → `job_log_signal` table
2. Reconciler reads `db_history` CSV + pulls AEE directory to HDD → `parse_exp_main_summary` reads **`ZZ_INTERNAL`** first (CSV: parts[0]=exp_class, parts[7]=process), falls back to `__exp_main.txt`
3. `log_signal.extra` carries `event_type`/`event_subtype`/`package_name`/`aee_ts`/`nfs_path`
4. Frontend `watcher-summary` endpoint aggregates by category → `AnomalyDashboard` / `WatcherSummaryCard`

`count_dbg_process.py` (in scan tool directory) is a standalone stats tool that also reads `ZZ_INTERNAL` — not integrated into platform code, but the parsing logic is aligned.

## Sprint 4 scan/upload/merge pipeline

- **ScanRunner** (`backend/agent/scan_runner.py`): calls `start_log_scan.py -m 5 -d {hdd_root} -side {side} [-end]` — full scan mode (NOT `-dedup_org`). Produces `Result_*_org.xls` on HDD.
- **UploadManager** (`backend/agent/upload_manager.py`): copies `_org.xls` → NFS `dedup/{run_id}/`, event dirs → NFS `devices/{run_id}/`. Auto-discovery uses `iterdir()` + `YYYY-MM-DD_HH-MM-SS_*` regex (depth=1, no recursion).
- **Control-plane merge** (`dedup_scan.py:run_merge_sync`): calls `start_log_scan.py -merge_files {a.xls} {b.xls} -side shanghai` — runs on backend, reads from NFS `dedup/`.
- **NFS path convention**: `{STP_AEE_NFS_ROOT}/dedup/{run_id}/` (scan reports) + `devices/{run_id}/` (event dirs) + `jira/{run_id}/` (extract output).
- **reload_config**: `POST /api/v1/plan-runs/hosts/{host_id}/reload-config` emits SocketIO command to re-read env vars without Agent restart. `ScanRunner`/`UploadManager` support `configure(force=True)`.

## Key env vars

| Var | Where | Purpose |
|-----|-------|---------|
| `STP_AEE_NFS_ROOT` | Backend + Agent | NFS/CIFS root for dedup/devices/jira (shared path) |
| `STP_DEDUP_SCAN_PYTHON` | Backend + Agent | Python interpreter for scan tool |
| `STP_DEDUP_SCAN_SCRIPT` | Backend + Agent | `start_log_scan.py` path (on NFS/CIFS share) |
| `STP_AEE_LOCAL_ROOT` | Agent | HDD root for AEE events (e.g. `/mnt/hdd/aee_events`) |
| `STP_SCRIPT_ROOT` | Backend | Script catalog scan source (must set in dev) |
| `STP_WATCHER_ENABLED` | Agent | Watcher subsystem gate (default `true`) |
| `STP_DEDUP_AUTO_SCAN` | Backend | Terminal auto-dedup trigger (default `1`) |
| `AUTO_ARCHIVE_POLL_INTERVAL_SECONDS` | Backend | auto_archive_sweep interval (default 120) |

See `backend/.env.example` and `backend/agent/.env.example` for full list.

## Key conventions

- Only `script:<name>` action type is supported in pipeline_def (see `CLAUDE.md` §Pipeline).
- Script `default_params` are immutable after creation — `PUT` returns 422. New version via `POST /api/v1/scripts/{name}/versions`.
- DB table names are singular (`device`, `host`, `plan`, `plan_run_artifact`).
- `frontend/src/utils/api/types.ts` is the canonical frontend type source — keep in sync with backend Pydantic schemas.
- WSL Agent needs `ANDROID_ADB_SERVER_PORT=5039`.
- Production Agent needs `AGENT_SECRET` env for SocketIO auth.
- `host.max_concurrent_jobs` column removed (migration `q2r3s4t5u6v7w8`). Capacity = `min(MAX_CONCURRENT_TASKS - active, heartbeat effective_slots)`.
- Pydantic v2 only — no `.dict()`/`parse_obj`/`from_orm`/`class Config`. Use `model_dump()`/`model_validate()`/`ConfigDict(from_attributes=True)`.
- `ORMBaseModel` (`backend/api/schemas/base.py`) auto-serializes datetime to ISO-UTC via `field_serializer(when_used="json")`.

## CI pipeline (`.github/workflows/ci.yml`)

Backend: `compileall backend/` → `pytest backend/tests/` (PostgreSQL service). Frontend: `tsc --noEmit` → `npm run build`. Docker build after both pass.

## Documentation

- **Entry**: [`docs/DOC-MAP.md`](docs/DOC-MAP.md) — PRD / ADR / design / acceptance layers.
- **Hub**: [`docs/README.md`](docs/README.md) — full documentation center.
- **Design**: [`docs/design/`](docs/design/) — system, backend, frontend, agent (aligned with code).
- **ADR-0025**: [`docs/adr/ADR-0025-phase4-architecture-alignment.md`](docs/adr/ADR-0025-phase4-architecture-alignment.md) — Plan C architecture.
- **Pipeline timing**: [`docs/design/06-realtime-and-background.md`](docs/design/06-realtime-and-background.md) §9 — scan/upload/merge sequence + five-trigger table.
- **Acceptance**: [`docs/acceptance/`](docs/acceptance/) — Sprint 2/3/4 matrices + real-device verification template.
