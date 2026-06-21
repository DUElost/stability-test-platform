# Design: Task Pipeline Engine + Real-time Log Streaming

## Context

The platform currently executes test tasks via a rigid `BaseTestCase` lifecycle hardcoded in `test_stages.py`. Logs flow from agent to backend through HTTP heartbeat batches (10s intervals, 200-line buffer), then broadcast to the frontend via WebSocket. The frontend renders logs in a basic `LogViewer` with a 1000-line cap and no step-level organization.

This design transforms the execution model into a user-defined pipeline with real-time log streaming, touching all three layers: Agent (execution engine), Backend (data + relay), and Frontend (visualization).

Key architectural constraints:
- Single-process FastAPI backend with in-memory WebSocket connection manager
- Agent runs on Linux under systemd, currently synchronous single-thread
- PostgreSQL database with existing `TaskRun` model (run-level only, no sub-steps)
- 7 existing `BaseTestCase` subclasses that must be fully migrated

## Goals / Non-Goals

**Goals:**
- Replace hardcoded `BaseTestCase` lifecycle with user-defined pipeline (Phase -> Step)
- Achieve millisecond-level log latency via Agent WebSocket long connection
- Deliver GitHub Actions-style UI with step tree, xterm.js terminal, and pipeline editor
- Maintain backward compatibility during migration (old tasks without `pipeline_def` still work)

**Non-Goals:**
- Multi-host distributed pipeline (steps execute on a single agent host)
- DAG-based arbitrary dependency graphs (topology is phase-serial + intra-phase parallel)
- Backend horizontal scaling (remains single-process; no Redis/message bus for WS fanout)
- Log persistence to external storage (S3/NFS); logs remain in DB + WebSocket relay
- Agent-side cancellation signals (out of scope; agent still runs to completion or timeout)

## Decisions

### DEC-1: Pipeline Definition Format — JSON stored in `Task.pipeline_def`

**Choice**: Pipeline definitions stored as a JSON column on the `Task` model, validated by a shared JSON Schema.

**Alternatives considered**:
- *YAML files on disk*: More human-readable but requires file sync between frontend/backend/agent. JSON is native to PostgreSQL JSONB, API payloads, and JavaScript.
- *Separate `pipeline` table with normalized rows*: Over-engineering for a tree structure that is always read/written atomically. JSON column is simpler and the pipeline is never queried by individual step fields.

**Structure**:
```json
{
  "version": 1,
  "phases": [
    {
      "name": "prepare",
      "parallel": false,
      "steps": [
        {
          "name": "check_device",
          "action": "builtin:check_device",
          "params": {},
          "timeout": 30,
          "on_failure": "stop"
        },
        {
          "name": "clean_env",
          "action": "builtin:clean_env",
          "params": { "uninstall_packages": ["com.test.app"] },
          "timeout": 60,
          "on_failure": "stop"
        }
      ]
    },
    {
      "name": "execute",
      "parallel": false,
      "steps": [
        {
          "name": "run_monkey",
          "action": "builtin:start_process",
          "params": { "command": "monkey -p com.app --throttle 500 -v 100000" },
          "timeout": 7200,
          "on_failure": "stop"
        }
      ]
    }
  ]
}
```

**`action` field resolution**:
- `builtin:<name>` — maps to a Python function in `backend/agent/actions/` module
- `tool:<tool_id>` — loads a registered Tool (via existing `tool_discovery` system), executes as a single atomic step
- `shell:<command>` — runs an ADB shell command directly (simple utility steps)

**Rationale**: The `action` prefix scheme provides clear namespacing and allows the existing tool system (`tool_id` + `tool_snapshot`) to coexist with new built-in actions without collision.

### DEC-2: Database Schema — New `run_steps` table, not reusing `workflow_steps`

**Choice**: Create a new `run_steps` table linked to `task_runs`, separate from the existing `workflow_steps` table.

**Alternatives considered**:
- *Reuse `workflow_steps`*: The existing `WorkflowStep` model orchestrates across tasks (each step creates a `TaskRun`). Our sub-steps are within a single `TaskRun`. The cardinality and lifecycle are fundamentally different — forcing them into one table would require confusing nullable foreign keys and ambiguous status semantics.
- *Store step status in `TaskRun.extra` JSON*: Would avoid schema migration but makes querying individual step history impossible and loses type safety.

**Schema**:
```sql
CREATE TABLE run_steps (
    id              SERIAL PRIMARY KEY,
    run_id          INTEGER NOT NULL REFERENCES task_runs(id) ON DELETE CASCADE,
    phase           VARCHAR(64) NOT NULL,       -- "prepare", "execute", "post_process"
    step_order      INTEGER NOT NULL,           -- order within phase
    name            VARCHAR(128) NOT NULL,
    action          VARCHAR(256) NOT NULL,       -- "builtin:check_device", "tool:42"
    params          JSONB DEFAULT '{}',
    status          VARCHAR(16) NOT NULL DEFAULT 'PENDING',
    started_at      TIMESTAMP,
    finished_at     TIMESTAMP,
    exit_code       INTEGER,
    error_message   TEXT,
    log_line_count  INTEGER DEFAULT 0,
    created_at      TIMESTAMP DEFAULT NOW()
);

CREATE INDEX ix_rs_run_id ON run_steps(run_id);
CREATE INDEX ix_rs_run_status ON run_steps(run_id, status);
```

**TaskRun status aggregation rule**:
- All steps COMPLETED → run COMPLETED
- Any step FAILED with `on_failure=stop` → run FAILED (remaining steps → CANCELED)
- Any step FAILED with `on_failure=continue` → continue; run status determined by final outcome
- Any step FAILED with `on_failure=retry` → retry up to N times, then treat as `stop`

### DEC-3: Agent Architecture — Threading-based pipeline engine, not asyncio

**Choice**: Keep the agent as a synchronous Python process but introduce a `PipelineEngine` class that uses `concurrent.futures.ThreadPoolExecutor` for intra-phase parallel steps.

**Alternatives considered**:
- *Full asyncio rewrite*: Would require rewriting all ADB interactions (subprocess calls), the HTTP client, and the heartbeat loop. The agent's primary work is spawning subprocesses and waiting — threads are a natural fit and the existing `subprocess.run()` calls work unchanged.
- *multiprocessing*: Adds IPC complexity for negligible benefit since steps are I/O-bound (ADB, file transfer), not CPU-bound.

**Architecture**:
```
main.py (main thread)
  ├── HeartbeatThread (daemon) — sends host heartbeat every POLL_INTERVAL
  ├── LockRenewalManager (daemon) — renews device locks every 60s
  └── Task execution loop
        └── PipelineEngine.execute(pipeline_def, context)
              ├── Phase 1 (serial): ThreadPoolExecutor for parallel steps
              ├── Phase 2 (serial): ThreadPoolExecutor for parallel steps
              └── Phase N...
```

**Key change**: Host heartbeat moves from the main loop into an independent daemon thread, ensuring it continues even during long task execution. This directly mitigates R-1.

### DEC-4: Log Transport — Agent WebSocket with HTTP heartbeat fallback

**Choice**: Agent opens a persistent WebSocket connection to `WS /ws/agent/{host_id}` on startup. All log lines are sent through this connection in real-time. If the WebSocket connection fails, the agent falls back to the existing HTTP heartbeat mechanism.

**Alternatives considered**:
- *Pure HTTP with shorter intervals (1-2s)*: Would increase backend load by 5-10x (from 0.1 req/s to 0.5-1 req/s per agent) and still have >1s latency.
- *SSE*: Agent is the data source, not the consumer. SSE is server→client, so the direction is wrong. We'd need the agent to run an HTTP server, which inverts the architecture.
- *gRPC streaming*: Adds significant dependency complexity (protobuf, grpcio) for a problem that WebSocket solves natively with the `websockets` library (already well-tested, minimal footprint).

**Protocol**:
```
Agent → Backend (WS /ws/agent/{host_id}):
  AUTH:     {"type": "auth", "agent_secret": "..."}
  LOG:      {"type": "log", "run_id": 42, "step_id": 7, "seq": 1234, "level": "INFO", "ts": "...", "msg": "..."}
  STEP:     {"type": "step_update", "run_id": 42, "step_id": 7, "status": "RUNNING", "progress": 35}
  HEARTBEAT:{"type": "heartbeat", "host_id": 1, "stats": {...}}

Backend → Agent (WS /ws/agent/{host_id}):
  ACK:      {"type": "ack", "last_seq": 1234}
  (future: CANCEL, CONFIG_UPDATE)
```

**Backend relay**: On receiving a `LOG` message from an agent, the backend broadcasts it to all frontend clients subscribed to `/ws/logs/{run_id}`. The existing `ConnectionManager.broadcast()` is reused.

**Fallback mechanism**: The agent tracks `_ws_connected: bool`. If `False`, `_log()` calls append to `_log_buffer` and flush via HTTP heartbeat (current behavior). On WS reconnect, buffered lines are replayed with their original sequence numbers.

### DEC-5: Agent Action System — Flat function registry, not class hierarchy

**Choice**: Built-in actions are plain Python functions with a standard signature, registered in a dictionary. This replaces the `BaseTestCase` class hierarchy for new pipeline-based execution.

**Alternatives considered**:
- *Keep BaseTestCase subclasses and wrap them*: Would perpetuate the rigid lifecycle. Since the decision is full migration, a clean break is preferred.
- *Action classes with inheritance*: Over-engineering for what are essentially stateless functions that receive context and return a result.

**Action signature**:
```python
# backend/agent/actions/base.py
@dataclass
class StepContext:
    adb: AdbWrapper
    serial: str
    params: dict
    run_id: int
    step_id: int
    logger: StepLogger  # wraps WS log sending

@dataclass
class StepResult:
    success: bool
    exit_code: int = 0
    error_message: str = ""
    metrics: dict = field(default_factory=dict)

# backend/agent/actions/device_actions.py
def check_device(ctx: StepContext) -> StepResult:
    ...

def clean_env(ctx: StepContext) -> StepResult:
    ...
```

**Registration**:
```python
# backend/agent/actions/__init__.py
ACTION_REGISTRY: Dict[str, Callable[[StepContext], StepResult]] = {
    "check_device": device_actions.check_device,
    "clean_env": device_actions.clean_env,
    "push_resources": device_actions.push_resources,
    "start_process": process_actions.start_process,
    "monitor_process": process_actions.monitor_process,
    "stop_process": process_actions.stop_process,
    "adb_pull": file_actions.adb_pull,
    "run_tool_script": tool_actions.run_tool_script,
    "aee_extract": log_actions.aee_extract,
    "log_scan": log_actions.log_scan,
}
```

**Tool migration path**: Each `BaseTestCase` subclass's `setup()`, `execute()`, `scan_risks()`, `collect()`, `teardown()` methods become individual action functions. The class is decomposed, not wrapped.

### DEC-6: Frontend — xterm.js per-step with instance pooling

**Choice**: Use xterm.js for log rendering. Maintain a pool of max 3 xterm instances (current step + 2 cached). When user switches to a new step, reuse the least-recently-used instance and write the new step's buffered log history into it.

**Alternatives considered**:
- *One xterm per step*: A pipeline with 10 steps would create 10 Canvas elements. Memory consumption grows linearly and exceeds 500MB for long-running tasks.
- *Single xterm, clear and rewrite on switch*: Simpler but causes visible flicker and loses scroll position.
- *Virtual list (react-window)*: Cannot render ANSI escape sequences natively. Would require a custom renderer that defeats the purpose of using a terminal component.

**Integration pattern**:
```
TaskDetails page (restructured)
├── Left panel: <PipelineStepTree>
│   ├── Phase "prepare" (collapsible)
│   │   ├── Step "check_device" [status icon] [duration]
│   │   └── Step "clean_env" [status icon] [duration]
│   └── Phase "execute" (collapsible)
│       └── Step "run_monkey" [status icon] [duration]
└── Right panel: <XTermLogViewer stepId={selectedStepId}>
    ├── Toolbar: search, download, auto-scroll toggle, level filter
    └── xterm.js Canvas (dynamically imported)
```

**xterm.js addons**: `@xterm/addon-search` (regex search), `@xterm/addon-fit` (auto-resize), `@xterm/addon-web-links` (clickable URLs).

**Log group folding**: Implemented via ANSI escape markers. The agent emits `\x1b]633;A\x07<group_title>` to start a fold and `\x1b]633;B\x07` to end it. The frontend intercepts these markers and renders collapsible sections in a thin overlay above the xterm canvas.

### DEC-7: WebSocket Message Protocol — Single multiplexed connection per frontend client

**Choice**: The frontend maintains a single WebSocket connection to `/ws/logs/{run_id}`. This connection receives log lines for ALL steps of that run, tagged with `step_id`. The frontend demultiplexes client-side by `step_id` into per-step buffers.

**Alternatives considered**:
- *One WS connection per step*: Browsers limit concurrent WebSocket connections (typically 6 per domain). A 10-step pipeline would exhaust the connection pool.
- *Separate `/ws/logs/{run_id}/step/{step_id}` endpoints*: Server-side complexity for routing with no real benefit since all data flows to the same browser tab anyway.

**Message format (backend → frontend)**:
```json
{"type": "STEP_LOG", "step_id": 7, "seq": 1234, "level": "INFO", "ts": "2026-02-23T01:23:45.678Z", "msg": "Starting monkey process..."}
{"type": "STEP_UPDATE", "step_id": 7, "status": "RUNNING", "progress": 35, "started_at": "..."}
{"type": "PHASE_UPDATE", "phase": "execute", "status": "RUNNING"}
{"type": "RUN_UPDATE", "status": "RUNNING", "progress": 55}
```

This extends the existing message types (`LOG`, `PROGRESS`, `RUN_UPDATE`) with step-aware variants. The frontend's `useWebSocket` hook handles both old-format (no `step_id`) and new-format messages for backward compatibility.

## Risks / Trade-offs

| Risk | Mitigation |
|------|------------|
| **Agent sync→thread migration breaks ADB interactions** | ADB commands are inherently serialized per device (USB protocol). ThreadPoolExecutor with `max_workers=1` for same-device steps ensures no concurrent ADB conflicts. Parallel steps on different devices (future) can use higher concurrency. |
| **WebSocket connection instability through corporate proxies** | Exponential backoff reconnection (1s→2s→4s→...→30s cap). HTTP heartbeat fallback guarantees log delivery even if WS never connects. `ping/pong` frames at 30s intervals to keep connection alive through proxies. |
| **xterm.js bundle size (~100KB gzip)** | Dynamic import via `React.lazy()`. xterm.js is only loaded when user navigates to TaskDetails page. Vite manual chunk config isolates it from vendor bundle. |
| **7 BaseTestCase tools must all be rewritten** | Migrate in priority order: AIMonkeyTest (most complex, validates architecture) → MonkeyTest/MonkeyAEEStabilityTest → remaining 4. Each tool gets its own migration PR with regression tests. |
| **Database write amplification from per-step status updates** | Batch status updates: agent sends step status changes every 2s or on transition, not per-log-line. `run_steps` table uses minimal indexes. Step log content goes through WebSocket only, not persisted per-line in DB. |
| **Memory growth from multiple xterm instances** | Instance pool (max 3). When a 4th step is selected, the oldest instance is `dispose()`d and recreated. Each xterm instance's `scrollback` is capped at 10,000 lines. |

## Migration Plan

### Deployment Sequence

1. **Database migration**: Run Alembic migration to add `run_steps` table and `Task.pipeline_def` column. Non-destructive — existing data untouched.
2. **Backend deploy**: New API endpoints (`/api/v1/runs/{id}/steps`) and WS endpoint (`/ws/agent/{host_id}`). Old endpoints remain functional.
3. **Agent deploy (rolling)**: New agent version supports both pipeline-based and legacy execution. If `pipeline_def` is present on a run, use pipeline engine; otherwise fall back to legacy `BaseTestCase` path.
4. **Frontend deploy**: New TaskDetails page detects `pipeline_def` presence. If absent, renders legacy view (current LogViewer). If present, renders pipeline step tree + xterm.js.

### Rollback Strategy

- **Database**: `run_steps` table can be dropped without affecting existing tables. `Task.pipeline_def` column is nullable.
- **Backend**: Old API endpoints are never removed during migration. Revert to previous backend version if issues arise.
- **Agent**: The `pipeline_def` detection branch means old task definitions continue to work on new agent. Reverting agent version is safe since old tasks don't use pipeline features.
- **Frontend**: Feature detection (`pipeline_def` presence) means the old UI path is always available.

## Open Questions

1. **Log retention policy**: How long should `run_steps` records and their associated log line counts be retained? Should there be a periodic cleanup job?
2. **Step-level artifacts**: Should individual steps be able to declare output artifacts (files), or only the entire run? This affects the `LogArtifact` model relationship.
3. **Pipeline versioning**: When a template's `pipeline_def` is updated, should running tasks continue with the old version or be affected? (Current `tool_snapshot` pattern suggests snapshot-at-creation.)
