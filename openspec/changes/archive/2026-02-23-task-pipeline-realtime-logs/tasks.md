## 1. Database Schema & Migrations

- [x] 1.1 Add `pipeline_def` JSONB nullable column to `tasks` table in ORM model (`backend/models/schemas.py`)
- [x] 1.2 Create `RunStep` ORM model with fields: id, run_id (FK), phase, step_order, name, action, params (JSONB), status (Enum), started_at, finished_at, exit_code, error_message, log_line_count, created_at
- [x] 1.3 Add `RunStepStatus` enum: PENDING, RUNNING, COMPLETED, FAILED, SKIPPED, CANCELED
- [x] 1.4 Add indexes: `ix_rs_run_id` on run_id, `ix_rs_run_status` on (run_id, status)
- [x] 1.5 Create Alembic migration script for `run_steps` table + `tasks.pipeline_def` column
- [x] 1.6 Add Pydantic schemas: `RunStepCreate`, `RunStepRead`, `RunStepUpdate` in `backend/api/schemas.py`
- [ ] 1.7 Verify migration forward/rollback on dev database

## 2. Pipeline Definition Schema

- [x] 2.1 Create JSON Schema file `backend/schemas/pipeline_schema.json` defining the pipeline_def structure (phases, steps, action, params, timeout, on_failure, parallel)
- [x] 2.2 Create Python validation utility `backend/core/pipeline_validator.py` that validates pipeline_def against the JSON Schema (using `jsonschema` library)
- [x] 2.3 Add pipeline_def validation to task creation endpoint (`backend/api/routes/tasks.py` `create_task()`)
- [x] 2.4 Add `pipeline_def` field to `TaskCreate` Pydantic schema (optional)
- [ ] 2.5 Write unit tests for schema validation: valid pipeline, missing fields, invalid action format, empty phases

## 3. Backend RunStep API

- [x] 3.1 Create `GET /api/v1/runs/{run_id}/steps` endpoint returning all RunStep records ordered by phase + step_order
- [x] 3.2 Create `GET /api/v1/runs/{run_id}/steps/{step_id}` endpoint returning single RunStep detail
- [x] 3.3 Create `POST /api/v1/agent/runs/{run_id}/steps/{step_id}/status` endpoint for agent HTTP fallback step status updates
- [x] 3.4 Add RunStep record creation logic: when a pipeline TaskRun starts, create PENDING RunStep records for all steps defined in pipeline_def
- [x] 3.5 Implement TaskRun status aggregation: derive run status from step statuses (all COMPLETED -> FINISHED, any FAILED+stop -> FAILED, FAILED+continue -> FINISHED with warning)
- [x] 3.6 Add `STEP_UPDATE` broadcast to frontend WebSocket subscribers when RunStep status changes
- [ ] 3.7 Write API tests for RunStep CRUD and status aggregation

## 4. Backend Agent WebSocket Endpoint

- [x] 4.1 Create `WS /ws/agent/{host_id}` endpoint in `backend/api/routes/websocket.py`
- [x] 4.2 Implement agent authentication: parse `{"type": "auth", "agent_secret": "..."}` message, validate against `AGENT_SECRET` env var, close with code 4001 on failure
- [x] 4.3 Implement `log` message handler: receive agent log messages, relay to `/ws/logs/{run_id}` as `STEP_LOG` format with step_id
- [x] 4.4 Implement `step_update` message handler: update RunStep DB record + broadcast `STEP_UPDATE` to frontend subscribers
- [x] 4.5 Implement `heartbeat` message handler: update host heartbeat (reuse existing heartbeat logic)
- [x] 4.6 Add ping/pong keepalive (30s interval) for agent connections
- [x] 4.7 Track connected agents in ConnectionManager (agent_id -> WebSocket mapping)
- [ ] 4.8 Write integration tests for agent WS auth, log relay, and step update flows

## 5. Agent WebSocket Client

- [x] 5.1 Add `websockets>=12.0` to agent dependencies in `install_agent.sh` pip install step
- [x] 5.2 Create `backend/agent/ws_client.py`: WebSocket client class with connect, send, reconnect methods
- [x] 5.3 Implement authentication handshake: send auth message on connect, wait for ack
- [x] 5.4 Implement exponential backoff reconnection (1s -> 2s -> 4s -> ... -> 30s cap)
- [x] 5.5 Implement message buffering: queue up to 1000 messages during disconnect, replay on reconnect with original seq numbers
- [x] 5.6 Implement ping/pong keepalive (30s interval, 10s pong timeout)
- [x] 5.7 Create `StepLogger` class that wraps WS client for per-step log sending (run_id, step_id, auto-seq)
- [x] 5.8 Implement HTTP fallback: detect WS unavailable, switch to existing HTTP heartbeat log delivery
- [ ] 5.9 Write unit tests for WS client reconnection, buffering, and fallback logic

## 6. Agent Pipeline Engine

- [x] 6.1 Create `backend/agent/pipeline_engine.py`: `PipelineEngine` class with `execute(pipeline_def, context)` method
- [x] 6.2 Implement phase-serial execution: iterate phases in order, block until all steps in current phase complete
- [x] 6.3 Implement intra-phase parallel execution: use `concurrent.futures.ThreadPoolExecutor` when phase `parallel: true`
- [x] 6.4 Implement step lifecycle: create StepContext, invoke action, capture StepResult, update RunStep status via WS/HTTP
- [x] 6.5 Implement `on_failure` policies: stop (cancel remaining), continue (log and proceed), retry (up to max_retries with delay)
- [x] 6.6 Implement step timeout enforcement via `concurrent.futures.Future.result(timeout=)`
- [x] 6.7 Integrate PipelineEngine into agent main loop: detect `pipeline_def` on TaskRun, route to engine vs legacy executor
- [ ] 6.8 Write integration test: 3-phase pipeline with mix of serial/parallel steps, failure scenarios

## 7. Agent Built-in Actions

- [x] 7.1 Create `backend/agent/actions/` package with `base.py` defining `StepContext`, `StepResult`, and `ACTION_REGISTRY`
- [x] 7.2 Implement `check_device` action: verify ADB connectivity via `adb shell echo test`
- [x] 7.3 Implement `clean_env` action: uninstall packages, clear logs, set system properties
- [x] 7.4 Implement `push_resources` action: push file list via `adb push`
- [x] 7.5 Implement `start_process` action: start command via `adb shell nohup`, capture PID, store in step metrics
- [x] 7.6 Implement `monitor_process` action: periodic alive check, log path monitoring, error file pull
- [x] 7.7 Implement `stop_process` action: kill by PID via `adb shell kill -9`
- [x] 7.8 Implement `adb_pull` action: pull remote directory/file to local path
- [x] 7.9 Implement `aee_extract` action: invoke aee_extract tool binary for db log decryption
- [x] 7.10 Implement `log_scan` action: scan files for keywords, deduplicate, generate report
- [x] 7.11 Implement `run_tool_script` action: load Tool class from tool_snapshot, execute as atomic step
- [ ] 7.12 Write unit tests for each action with mocked ADB wrapper

## 8. Agent Host Heartbeat Thread

- [x] 8.1 Refactor `backend/agent/main.py`: extract heartbeat sending into a dedicated `HeartbeatThread` daemon class
- [x] 8.2 HeartbeatThread runs device discovery + system monitor + heartbeat POST every `POLL_INTERVAL`, independent of task loop
- [x] 8.3 Integrate heartbeat with WS client: if WS connected, send heartbeat via WS; otherwise use HTTP POST
- [x] 8.4 Ensure task execution loop no longer blocks heartbeat delivery
- [x] 8.5 Write test verifying heartbeat continues during simulated long-running task

## 9. Migrate Existing Tools to Step Actions

- [x] 9.1 Decompose `AIMonkeyTest` into step actions: extract setup/fill_storage/execute/monitor/scan_risks/collect/teardown into individual action functions
- [x] 9.2 Create default pipeline template for AIMONKEY test type (prepare: check_device+clean_env+push_resources+fill_storage, execute: start_process+monitor_process, post_process: stop_process+adb_pull+aee_extract+log_scan)
- [x] 9.3 Decompose `MonkeyTest` into step actions + default pipeline template
- [x] 9.4 Decompose `MonkeyAEEStabilityTest` into step actions + default pipeline template
- [x] 9.5 Decompose `MtbfTest` into step actions + default pipeline template
- [x] 9.6 Decompose `DdrTest` into step actions + default pipeline template
- [x] 9.7 Decompose `GpuStressTest` into step actions + default pipeline template
- [x] 9.8 Decompose `StandbyTest` into step actions + default pipeline template
- [x] 9.9 Regression test each migrated tool: verify output matches legacy execution

## 10. Frontend: xterm.js Integration

- [x] 10.1 Install xterm.js dependencies: `xterm`, `@xterm/addon-search`, `@xterm/addon-fit`, `@xterm/addon-web-links`
- [x] 10.2 Create `frontend/src/components/log/XTerminal.tsx`: React wrapper for xterm.js with dynamic import (`React.lazy`)
- [x] 10.3 Implement ANSI color coding for log levels: ERROR=red, WARN=yellow, DEBUG=gray, INFO=default
- [x] 10.4 Implement keyword auto-highlighting: inject ANSI bold-red for FATAL/CRASH, bold-yellow for ANR
- [x] 10.5 Integrate `@xterm/addon-search`: search toolbar with text/regex toggle, next/previous navigation
- [x] 10.6 Integrate `@xterm/addon-fit`: auto-resize on container/window resize with 200ms debounce
- [x] 10.7 Implement auto-scroll with pause-on-manual-scroll and "Resume" button
- [x] 10.8 Implement log download: strip ANSI codes, save as `run_{id}_step_{name}.log`
- [x] 10.9 Implement xterm instance pool (max 3): LRU eviction via `terminal.dispose()`, cleanup on unmount
- [x] 10.10 Add Vite manual chunk config to isolate xterm.js from vendor bundle
- [x] 10.11 Write component tests for XTerminal rendering, search, and instance lifecycle

## 11. Frontend: Pipeline Step Tree Component

- [x] 11.1 Create `frontend/src/components/pipeline/PipelineStepTree.tsx`: collapsible phase groups with step list items
- [x] 11.2 Implement step status icons: pending (gray circle), running (blue spinner), completed (green check), failed (red X), skipped (gray strikethrough)
- [x] 11.3 Implement step duration display: elapsed time for completed steps, live counter for running steps
- [x] 11.4 Implement active step highlighting: blue left border on RUNNING step
- [x] 11.5 Implement phase auto-expand: expand phase containing RUNNING step, collapse others by default
- [x] 11.6 Implement step click handler: emit `onStepSelect(stepId)` callback to parent
- [x] 11.7 Consume `STEP_UPDATE` WebSocket messages to update step statuses in real-time
- [x] 11.8 Write component tests for step tree rendering, status transitions, and selection

## 12. Frontend: TaskDetails Page Restructure

- [x] 12.1 Add `GET /api/v1/runs/{run_id}/steps` to frontend API client (`frontend/src/utils/api.ts`) with `RunStep` TypeScript interface
- [x] 12.2 Add `STEP_LOG` and `STEP_UPDATE` message types to WebSocket message handling
- [x] 12.3 Implement per-step log buffering in a `Map<stepId, LogLine[]>` with 5000-line cap per step
- [x] 12.4 Implement step log demultiplexing: route incoming `STEP_LOG` messages to per-step buffers by `step_id`
- [x] 12.5 Restructure TaskDetails layout: detect RunStep presence -> pipeline layout (step tree left + xterm right) vs legacy layout (info left + LogViewer right)
- [x] 12.6 Wire PipelineStepTree `onStepSelect` to XTerminal: clear terminal, write buffered logs for selected step
- [x] 12.7 Implement auto-follow: when a new step transitions to RUNNING and no manual selection, auto-switch log panel
- [x] 12.8 Write integration tests for TaskDetails with mocked WebSocket messages

## 13. Frontend: Pipeline Editor

- [x] 13.1 Create `frontend/src/components/pipeline/PipelineEditor.tsx`: phase cards with step forms
- [x] 13.2 Implement add/remove phase with name editing and parallel toggle
- [x] 13.3 Implement add/remove step within phase: action type selector, params form, timeout input, on_failure dropdown
- [x] 13.4 Integrate `DynamicToolForm` for step parameter configuration based on action's JSON Schema
- [x] 13.5 Implement action type browser: dropdown listing built-in actions (grouped by category) + registered tools
- [x] 13.6 Install `@dnd-kit/core` + `@dnd-kit/sortable` and implement drag-and-drop reordering for steps and phases
- [x] 13.7 Implement JSON preview panel: live-updating pipeline_def JSON display with validation error highlighting
- [x] 13.8 Integrate into CreateTask flow: add "Configure Pipeline" step between tool selection and device selection
- [x] 13.9 Implement template save: "Save as Template" button writes pipeline_def to TaskTemplate API
- [x] 13.10 Implement template load: template selector populates editor with existing template's pipeline_def
- [x] 13.11 Add built-in pipeline templates for common test types (Monkey, AIMONKEY, MTBF)
- [x] 13.12 Write component tests for editor interactions, drag-drop, and JSON preview

## 14. Polish & Hardening

- [x] 14.1 Implement log fold groups: agent emits ANSI fold markers, frontend renders collapsible sections above xterm canvas
- [ ] 14.2 Implement WebSocket reconnection end-to-end test: disconnect agent mid-task, verify log continuity after reconnect
- [ ] 14.3 Performance test: run pipeline with 10K+ log lines, verify xterm renders at >30 FPS
- [ ] 14.4 Memory test: open 5 different step logs sequentially, verify xterm pool caps at 3 instances
- [x] 14.5 Update `install_agent.sh` to include `websockets` in pip requirements
- [x] 14.6 Update `backend/agent/.env.example` with new WS-related config variables
- [x] 14.7 Update CLAUDE.md with new architecture, endpoints, and pipeline_def documentation
- [ ] 14.8 Full regression test: create task with pipeline, execute on agent, verify step tree + log streaming + completion flow
