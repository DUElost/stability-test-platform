## ADDED Requirements

### Requirement: Monkey AEE teardown pipeline template
The system SHALL provide a `monkey_aee_teardown.json` pipeline template in `backend/schemas/pipeline_templates/` that defines the standard teardown sequence for Monkey AEE stability tests.

#### Scenario: Teardown template file exists and is valid
- **WHEN** the backend starts and loads pipeline templates from `backend/schemas/pipeline_templates/`
- **THEN** `monkey_aee_teardown.json` SHALL exist, validate against the pipeline_schema, and be accessible via `GET /api/v1/pipeline/templates/monkey_aee_teardown`

#### Scenario: Teardown template step composition
- **WHEN** the `monkey_aee_teardown.json` template is loaded
- **THEN** it SHALL contain the following steps in order:
  - prepare: `ensure_root` (retry=1)
  - execute: `stop_process` (using `process_name`), `collect_bugreport`, `scan_aee` (incremental), `export_mobilelogs`
  - post_process: `aee_extract` (batch), `log_scan`, `adb_pull` (final full pull)

#### Scenario: Teardown template uses process_name for stop
- **WHEN** the `stop_process` step in the teardown template is executed
- **THEN** it SHALL use `process_name: "com.android.commands.monkey.transsion"` to kill the monkey process, not `pid_from_step` (since teardown runs as a separate job without access to init's shared context)

### Requirement: Teardown template accessible via API
The teardown template SHALL be accessible through the existing pipeline templates API endpoint.

#### Scenario: List templates includes teardown
- **WHEN** `GET /api/v1/pipeline/templates` is called
- **THEN** the response SHALL include `monkey_aee_teardown` in the template list

#### Scenario: Get specific teardown template
- **WHEN** `GET /api/v1/pipeline/templates/monkey_aee_teardown` is called
- **THEN** the response SHALL return the full template with `description`, `pipeline_def` containing all stages and steps
