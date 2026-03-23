## ADDED Requirements

### Requirement: Pipeline execution engine
The agent SHALL implement a `PipelineEngine` class that parses a pipeline definition and executes phases and steps according to the defined topology (phase-serial, intra-phase parallel via ThreadPoolExecutor).

#### Scenario: Full pipeline execution
- **WHEN** the agent receives a TaskRun with a valid `pipeline_def` containing 3 phases
- **THEN** the engine SHALL execute each phase in order, create `RunStep` records for each step, update their status transitions (PENDING -> RUNNING -> COMPLETED/FAILED), and aggregate the final TaskRun status

#### Scenario: Pipeline with parallel phase
- **WHEN** a phase declares `parallel: true` with 3 steps
- **THEN** the engine SHALL submit all 3 steps to a ThreadPoolExecutor and wait for all futures to complete before proceeding to the next phase

### Requirement: Built-in action registry
The agent SHALL provide a registry of built-in actions as plain Python functions with a standardized `StepContext -> StepResult` signature.

#### Scenario: Action lookup
- **WHEN** the engine encounters a step with `action: "builtin:check_device"`
- **THEN** the engine SHALL look up `check_device` in `ACTION_REGISTRY` and invoke it with a `StepContext` containing the ADB wrapper, device serial, step params, run_id, step_id, and a StepLogger

#### Scenario: Unknown action
- **WHEN** the engine encounters a step with `action: "builtin:nonexistent"`
- **THEN** the engine SHALL mark the step as FAILED with error_message "Unknown action: builtin:nonexistent" and apply the step's `on_failure` policy

### Requirement: Built-in device actions
The agent SHALL provide the following built-in actions for device interaction.

#### Scenario: check_device action
- **WHEN** `check_device` is invoked
- **THEN** it SHALL verify the device is reachable via `adb shell echo test`, return success if the command succeeds within timeout, or failure with error details

#### Scenario: clean_env action
- **WHEN** `clean_env` is invoked with params `{"uninstall_packages": ["com.test.app"], "clear_logs": true, "set_properties": {"persist.sys.debug": "1"}}`
- **THEN** it SHALL uninstall each listed package (ignoring "not installed" errors), clear device log directories if `clear_logs` is true, and set each system property via `adb shell setprop`

#### Scenario: push_resources action
- **WHEN** `push_resources` is invoked with params `{"files": [{"local": "/opt/resources/blacklist.txt", "remote": "/sdcard/blacklist.txt"}]}`
- **THEN** it SHALL push each file via `adb push` and return failure if any push fails

### Requirement: Built-in process actions
The agent SHALL provide built-in actions for process lifecycle management.

#### Scenario: start_process action
- **WHEN** `start_process` is invoked with params `{"command": "monkey -p com.app --throttle 500 -v 100000", "background": true}`
- **THEN** it SHALL start the command on the device via `adb shell nohup ... &`, capture the PID, and store it in step metrics for use by subsequent steps

#### Scenario: monitor_process action
- **WHEN** `monitor_process` is invoked with params `{"pid_from_step": "run_monkey", "check_interval": 5, "log_paths": ["/data/aee_exp/"], "pull_on_error": true}`
- **THEN** it SHALL periodically check if the process is alive, monitor the specified log paths for new error files, and pull error files via `adb pull` when detected

#### Scenario: stop_process action via pid_from_step
- **WHEN** `stop_process` is invoked with params `{"pid_from_step": "run_monkey"}`
- **THEN** it SHALL look up the PID from shared metrics, kill the process by PID via `adb shell kill -9 {pid}`, and return success even if the process has already exited

#### Scenario: stop_process action via process_name
- **WHEN** `stop_process` is invoked with params `{"process_name": "com.android.commands.monkey.transsion"}`
- **THEN** it SHALL find matching PIDs via `adb shell pgrep -f <process_name>`, kill each PID via `adb shell kill -9 {pid}`, log the number of processes killed, and return success even if no matching process is found

#### Scenario: stop_process priority — pid_from_step over process_name
- **WHEN** `stop_process` is invoked with both `pid_from_step` and `process_name` params
- **THEN** it SHALL use the PID from `pid_from_step` (via shared metrics) and ignore `process_name`

#### Scenario: stop_process with no PID and no process_name
- **WHEN** `stop_process` is invoked with neither `pid_from_step` nor `process_name`
- **THEN** it SHALL log "No PID or process_name to stop, skipping" and return success (no-op)

#### Scenario: stop_process process_name kills multiple instances
- **WHEN** `stop_process` is invoked with `process_name` and `pgrep -f` returns multiple PIDs
- **THEN** it SHALL kill ALL matching PIDs and log "Killed N processes matching <process_name>"

### Requirement: Built-in log processing actions
The agent SHALL provide built-in actions for post-test log processing.

#### Scenario: adb_pull action
- **WHEN** `adb_pull` is invoked with params `{"remote_path": "/data/aee_exp/", "local_path": "/opt/logs/run_42/"}`
- **THEN** it SHALL pull the remote directory/file to the local path via `adb pull` and report the number of files transferred

#### Scenario: aee_extract action
- **WHEN** `aee_extract` is invoked with params `{"input_dir": "/opt/logs/run_42/aee_exp/", "output_dir": "/opt/logs/run_42/decoded/"}`
- **THEN** it SHALL invoke the `aee_extract` tool binary to decrypt db log files in the input directory and write decoded output to the output directory

#### Scenario: log_scan action
- **WHEN** `log_scan` is invoked with params `{"input_dir": "/opt/logs/run_42/decoded/", "keywords": ["FATAL", "CRASH", "ANR"], "deduplicate": true}`
- **THEN** it SHALL scan all files in the input directory for keyword matches, deduplicate identical issues, and return a structured report as step metrics

### Requirement: Tool action adapter
The agent SHALL support executing registered Tools (from the existing tool_discovery system) as pipeline steps via the `tool:<tool_id>` action prefix.

#### Scenario: Execute registered tool as step
- **WHEN** a step declares `action: "tool:42"` and the TaskRun contains `tool_snapshot` for tool_id=42
- **THEN** the engine SHALL load the tool class from `tool_snapshot.script_path`, instantiate it, call its `execute()` method with the step's params, and map the result to a `StepResult`

### Requirement: Host heartbeat independent thread
The agent SHALL run host heartbeat reporting in a dedicated daemon thread, decoupled from the task execution loop.

#### Scenario: Heartbeat continues during long task
- **WHEN** a task runs for 30 minutes
- **THEN** the heartbeat thread SHALL continue sending heartbeats every `POLL_INTERVAL` seconds throughout the entire execution, preventing host offline false-positives

#### Scenario: Heartbeat thread failure isolation
- **WHEN** the heartbeat thread encounters a network error
- **THEN** the error SHALL be logged and retried on the next interval without affecting task execution

### Requirement: Legacy execution fallback
The agent SHALL support executing tasks without `pipeline_def` using the existing BaseTestCase lifecycle as a transitional path.

#### Scenario: Task without pipeline_def
- **WHEN** the agent receives a TaskRun whose associated Task has `pipeline_def = NULL`
- **THEN** the agent SHALL execute the task using the existing `TaskExecutor` code path (tool_id -> class registry -> legacy script)

### Requirement: Lock verification at pipeline start
The `PipelineEngine` SHALL verify that the device lock is held by the current job before executing any pipeline stages. Verification SHALL be performed by calling the `extend_lock` endpoint as a liveness check.

#### Scenario: Lock held by this job
- **WHEN** the pipeline engine starts and calls `extend_lock` for the current job
- **THEN** the extend succeeds, and the engine SHALL proceed with stage execution

#### Scenario: Lock not held (stolen or expired)
- **WHEN** the pipeline engine starts and calls `extend_lock` but receives a 409 response
- **THEN** the engine SHALL abort immediately with error `device_lock_not_held`, skip all stages, and report the job as FAILED

#### Scenario: Lock verification network failure
- **WHEN** the pipeline engine starts and the `extend_lock` call fails due to a network error
- **THEN** the engine SHALL retry up to 3 times with exponential backoff; if all retries fail, the engine SHALL abort with error `lock_verification_unreachable`

### Requirement: Lock release via completion endpoint
The `PipelineEngine` SHALL NOT call `release_lock` directly. Lock release SHALL occur server-side when the Agent reports job completion via `POST /api/v1/agent/jobs/{job_id}/complete`.

#### Scenario: Pipeline completes successfully
- **WHEN** the pipeline engine finishes all stages without fatal errors
- **THEN** it SHALL call `complete_run` with status `COMPLETED`, and the backend SHALL release the device lock

#### Scenario: Pipeline fails
- **WHEN** the pipeline engine encounters a fatal step failure
- **THEN** it SHALL call `complete_run` with status `FAILED`, and the backend SHALL release the device lock

#### Scenario: Pipeline aborted (lock lost mid-execution)
- **WHEN** the `LockRenewalManager` receives a 409 during periodic renewal (lock stolen)
- **THEN** it SHALL remove the run from `_active_run_ids`, and the pipeline engine SHALL detect the lost lock at the next step boundary and abort with status `ABORTED`

### Requirement: stop_process param_schema update
The `builtin_actions.json` catalog SHALL include a `process_name` field in the `stop_process` action's `param_schema`.

#### Scenario: param_schema includes process_name
- **WHEN** the `builtin_actions.json` file is loaded for `stop_process`
- **THEN** it SHALL contain a `process_name` field with type `string`, label `Process Name`, placeholder `com.example.app`, and description `Process name pattern for pgrep -f (used when pid_from_step is unavailable)`
