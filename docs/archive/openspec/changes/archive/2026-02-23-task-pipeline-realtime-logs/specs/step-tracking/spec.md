## ADDED Requirements

### Requirement: RunStep database table
The system SHALL create a `run_steps` table that records the execution state of each step within a TaskRun.

#### Scenario: RunStep record created on pipeline start
- **WHEN** the agent begins executing a pipeline
- **THEN** the backend SHALL create one `RunStep` record per step defined in the pipeline_def, all with initial status PENDING

#### Scenario: RunStep schema fields
- **WHEN** a RunStep record is created
- **THEN** it SHALL contain: `id`, `run_id` (FK to task_runs), `phase`, `step_order`, `name`, `action`, `params` (JSONB), `status`, `started_at`, `finished_at`, `exit_code`, `error_message`, `log_line_count`, `created_at`

### Requirement: RunStep status lifecycle
RunStep records SHALL follow the status lifecycle: PENDING -> RUNNING -> COMPLETED | FAILED | SKIPPED | CANCELED.

#### Scenario: Step starts executing
- **WHEN** the agent begins executing a step
- **THEN** the RunStep status SHALL transition from PENDING to RUNNING, and `started_at` SHALL be set to the current timestamp

#### Scenario: Step completes successfully
- **WHEN** a step finishes without error
- **THEN** the RunStep status SHALL transition from RUNNING to COMPLETED, `finished_at` SHALL be set, and `exit_code` SHALL be 0

#### Scenario: Step fails
- **WHEN** a step encounters an error
- **THEN** the RunStep status SHALL transition from RUNNING to FAILED, `finished_at` SHALL be set, `exit_code` SHALL be non-zero, and `error_message` SHALL contain the failure description

#### Scenario: Step skipped due to prior failure
- **WHEN** a preceding step fails with `on_failure: "stop"`
- **THEN** all remaining RunStep records in the current and subsequent phases SHALL be set to SKIPPED

#### Scenario: Step canceled
- **WHEN** the TaskRun is canceled by the user
- **THEN** all RunStep records with status PENDING or RUNNING SHALL be set to CANCELED

### Requirement: TaskRun status aggregation from steps
The TaskRun status SHALL be derived from the aggregate state of its RunStep records.

#### Scenario: All steps completed
- **WHEN** all RunStep records for a TaskRun have status COMPLETED
- **THEN** the TaskRun status SHALL be set to FINISHED

#### Scenario: Any step failed with stop policy
- **WHEN** any RunStep has status FAILED and the corresponding step's `on_failure` is "stop"
- **THEN** the TaskRun status SHALL be set to FAILED

#### Scenario: Step failed with continue policy, all others completed
- **WHEN** some RunStep records have status FAILED (with `on_failure: "continue"`) and all remaining steps are COMPLETED
- **THEN** the TaskRun status SHALL be set to FINISHED with a warning flag in `log_summary`

### Requirement: RunStep API endpoints
The backend SHALL expose REST endpoints for querying RunStep records.

#### Scenario: List steps for a run
- **WHEN** a GET request is made to `/api/v1/runs/{run_id}/steps`
- **THEN** the backend SHALL return all RunStep records for the given run, ordered by phase and step_order

#### Scenario: Get single step detail
- **WHEN** a GET request is made to `/api/v1/runs/{run_id}/steps/{step_id}`
- **THEN** the backend SHALL return the full RunStep record including params, error_message, and log_line_count

### Requirement: Step status update from agent
The backend SHALL accept step status updates from agents via both the WebSocket channel and HTTP fallback.

#### Scenario: Step update via WebSocket
- **WHEN** the backend receives a `step_update` message on `WS /ws/agent/{host_id}` with `{run_id, step_id, status, exit_code, error_message}`
- **THEN** the backend SHALL update the corresponding RunStep record and broadcast a `STEP_UPDATE` message to all frontend clients subscribed to `/ws/logs/{run_id}`

#### Scenario: Step update via HTTP fallback
- **WHEN** the agent sends a POST to `/api/v1/agent/runs/{run_id}/steps/{step_id}/status` with `{status, exit_code, error_message}`
- **THEN** the backend SHALL update the RunStep record and broadcast the status change to WebSocket subscribers

### Requirement: Database migration
The system SHALL provide an Alembic migration that creates the `run_steps` table and adds the `pipeline_def` column to the `tasks` table.

#### Scenario: Forward migration
- **WHEN** `alembic upgrade head` is executed
- **THEN** the `run_steps` table SHALL be created with all required columns and indexes, and `tasks.pipeline_def` JSONB nullable column SHALL be added

#### Scenario: Rollback migration
- **WHEN** `alembic downgrade -1` is executed
- **THEN** the `run_steps` table SHALL be dropped and the `tasks.pipeline_def` column SHALL be removed
