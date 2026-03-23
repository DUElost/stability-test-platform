## MODIFIED Requirements

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

## ADDED Requirements

### Requirement: stop_process param_schema update
The `builtin_actions.json` catalog SHALL include a `process_name` field in the `stop_process` action's `param_schema`.

#### Scenario: param_schema includes process_name
- **WHEN** the `builtin_actions.json` file is loaded for `stop_process`
- **THEN** it SHALL contain a `process_name` field with type `string`, label `Process Name`, placeholder `com.example.app`, and description `Process name pattern for pgrep -f (used when pid_from_step is unavailable)`
