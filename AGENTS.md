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

## 生产机调试约束

部分部署机上 **本机 PostgreSQL 即生产库**（如 `backend/.env` 的 `DATABASE_URL=...@localhost:5432/stp_dev`），而 **Docker testcontainers 仅用于隔离测试**。在生产机上改代码时务必遵守：

| 场景 | 做法 |
|------|------|
| 日常改码验证 | 优先 `pytest backend/agent/tests/`（不连 PG，~30s） |
| 必须跑 `backend/tests/` | 使用 **Docker testcontainers**（`conftest.py` 自动起临时 `postgres:16` 容器），**不要**把 `TEST_DATABASE_URL` 指到 `stp_dev` 或任何生产库名 |
| 迁移试验 | 禁止对生产库执行 `alembic upgrade` 试跑；在开发机/CI 或容器内验证 |
| 手工 API 冒烟 | 可连生产控制面，但避免破坏性写操作 |

**禁止示例**（会在生产数据上建表/清库/跑用例）：

```bash
# ❌ 切勿在生产机这样跑后端测试
export TEST_DATABASE_URL=postgresql+psycopg://...@127.0.0.1:5432/stp_dev
pytest backend/tests/
```

**推荐示例**（隔离 PG，与 CI 一致）：

```bash
# 用户须在 docker 组（一次性：sudo usermod -aG docker $USER && newgrp docker）
unset TEST_DATABASE_URL   # 让 conftest 走 testcontainers
JWT_SECRET_KEY=test-secret python -m pytest backend/tests/path/to/test.py -q
```

- 未设置 `TEST_DATABASE_URL` 时，`backend/tests/conftest.py` 通过 Docker 拉起**独立**测试库，测完销毁。
- 若 `docker ps` 报 `permission denied`，将当前用户加入 `docker` 组后**重新登录**（或 `newgrp docker`），不要用生产 `DATABASE_URL` 代替。
- `ALLOW_SQLITE_TESTS=1` 仅适合少量用例；`test_agent_dual_write.py` 等仍需 PostgreSQL partial unique index，不能替代完整 backend 套件。

## Test quirks

- Backend pytest needs `TEST_DATABASE_URL` (PostgreSQL). Set `ALLOW_SQLITE_TESTS=1` for local SQLite (no PG required, but `test_agent_dual_write.py` skips on SQLite — needs PG partial unique index).
- `os.environ["TESTING"] = "1"` is set in `backend/tests/conftest.py` — this disables Redis/SAQ/APScheduler startup in lifespan.
- Backend full-suite can timeout locally due to session-scoped engine fixture. Run single files: `pytest backend/tests/api/test_dedup_scan_endpoints.py -x`.
- Agent tests (`backend/agent/tests/`) are self-contained — no DB/Redis, fast (~30s for 600 tests). Control-plane tests that need DB go in `backend/tests/`, not `backend/agent/tests/`.
- Frontend tests use vitest + jsdom, `@/` path alias maps to `src/`.
- WATCHER_SIGNAL invalidation is debounced 2s in `PlanRunDetailPage.tsx` — tests asserting refetch need `waitFor({ timeout: 4000 })`.

## Architecture

- **app** = `socketio.ASGIApp(sio_server, fastapi_app)` — combined ASGI mount in `backend/main.py:196`.
- **Frontend pages** are `React.lazy()` loaded via `frontend/src/router/index.tsx`.
- **API client** modules in `frontend/src/utils/api/` (`planRuns.ts`, `hosts.ts`, `plans.ts`, etc.).
- **ADR-0020**: Plan/PlanStep replaced Workflow/TaskTemplate. No `plan.lifecycle` column — lifecycle composed from `PlanStep` rows + `patrol_interval_seconds`/`timeout_seconds` at dispatch time.
- **ADR-0018**: Watcher subsystem gated by `STP_WATCHER_ENABLED` (default `true`). Agent inotifyd monitors device AEE/ANR directories → `job_log_signal` table → frontend `watcher-summary`.
- **ADR-0025**: Plan C — Agent local scan + on-demand upload + control-plane merge. Scan tool is `start_log_scan.py` (external, deployed on 15.4 CIFS share). Three-phase archive: SSD→HDD→15.4 CIFS.
- **Agent** runs on Linux hosts, connects Android devices via ADB. Two enrollment paths: `install_agent.sh` (systemd) or dev `python -m backend.agent.main`.
- **SAQ pipeline** (Sprint 4): `scan_task` → `upload_task` → `merge_task` chain. `scan_task` polls NFS for all host artifacts before enqueuing follow-ups.

## AEE crash detection chain (初筛选)

两层互补，Reconciler 为主、inotifyd 为兜底：

### Reconciler（主路径，默认开）

每 60s 基线周期（`STP_WATCHER_AEE_RECONCILE_ENABLED=true`，默认开启）：
1. `adb shell cat /data/aee_exp/db_history` + `/data/vendor/aee_exp/db_history` → sha256 对比判断是否变化
2. 新行 → `adb pull` 整目录到 Agent HDD
3. 读 **`ZZ_INTERNAL`** 优先解析（CSV：parts[0]=exp_class, parts[7]=cur_process）
4. 读 `__exp_main.txt` fallback
5. `SignalEmitter.emit(source="reconciler")` → `extra={event_type, event_subtype, package_name, aee_ts, nfs_path}`

日志标记：`aee_reconciler_emit`（DEBUG 级，含 `pkg=` / `subtype=`）

### inotifyd（兜底路径）

Reconciler 启动失败时自动回退：
1. `adb shell inotifyd - /data/aee_exp:nwx /data/vendor/aee_exp:nwx` 实时监听
2. 文件创建/写入 → `SignalEmitter.emit(source="inotifyd")`
3. 不读 ZZ_INTERNAL，`extra` 为 NULL（仅提供计数）

日志标记：`device_log_watcher_emit_fallback`（INFO 级，表示兜底激活）
回退标记：`aee_reconciler_emit_rollback`（WARNING 级，表示 Reconciler 启动失败）

### 监测目录

仅 `/data/aee_exp` + `/data/vendor/aee_exp`（MTK 平台 `/data/aee_exp` 包含 ANR 信息，`/data/anr` 不再监测）。

### 数据流

```
ZZ_INTERNAL / __exp_main.txt → SignalEmitter → local SQLite outbox
  → POST /agent/log-signals → job_log_signal 表 (extra JSONB)
  → Frontend watcher-summary (按 category/package 聚合)
  → AnomalyDashboard (双饼图 + 包名榜) / WatcherSummaryCard (异常率进度条)
```

### 风险评级

`aggregate_risk_summary_from_signals` 从 `job_log_signal.extra->>'event_subtype'` 聚合，按 `_RISK_RATING_RULES` 定级 S/A/B：

| 级别 | 触发条件 |
|------|---------|
| **S**（致命） | SWT / Fatal NE / Fatal JE / HWT / Kernel (KE) / HW Reboot / HANG — 任 1 次 |
| **A**（高） | ANR ≥ 10 / JE ≥ 3 / NE ≥ 2 / Java ≥ 3 |
| **B**（低） | 其余非零 |

`count_dbg_process.py`（scan tool 目录下）独立统计工具，同样读 ZZ_INTERNAL，不与平台代码集成但解析逻辑对齐。

## Sprint 4 scan/upload/merge pipeline

- **ScanRunner** (`backend/agent/scan_runner.py`): calls `start_log_scan.py -m 0 -d {hdd_root} -side {side} [-end]` — AEE_TNE mode (scans HDD, no external DB deps; NOT `-dedup_org`). Produces `Result_*_org.xls` on HDD.
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
