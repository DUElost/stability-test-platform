## ADDED Requirements

### Requirement: Pipeline definition JSON schema
The system SHALL define a versioned JSON Schema for pipeline definitions that describes phases, steps, action references, parameters, timeouts, and failure policies. The schema SHALL be shared between backend validation, agent parsing, and frontend editor.

#### Scenario: Valid pipeline with multiple phases
- **WHEN** a pipeline definition contains 3 phases (prepare, execute, post_process) each with 1-3 steps
- **THEN** the JSON Schema validation SHALL pass and the definition SHALL be accepted by both backend API and agent parser

#### Scenario: Step action type resolution
- **WHEN** a step declares `action: "builtin:check_device"`
- **THEN** the agent SHALL resolve it to the `check_device` function in the built-in action registry
- **WHEN** a step declares `action: "tool:42"`
- **THEN** the agent SHALL load the Tool with id=42 via the existing tool_discovery system and execute it as a single atomic step
- **WHEN** a step declares `action: "shell:echo hello"`
- **THEN** the agent SHALL execute `echo hello` as an ADB shell command on the target device

#### Scenario: Invalid pipeline definition rejected
- **WHEN** a pipeline definition is missing required fields (e.g., phase without `name`, step without `action`)
- **THEN** the backend API SHALL return HTTP 422 with a validation error message identifying the missing field

### Requirement: Pipeline stored on Task model
The system SHALL store the pipeline definition as a JSONB column `pipeline_def` on the `tasks` table. The column SHALL be nullable to maintain backward compatibility with existing tasks.

#### Scenario: Task created with pipeline
- **WHEN** a user creates a task with a `pipeline_def` payload
- **THEN** the backend SHALL validate the definition against the JSON Schema and store it in the `pipeline_def` column

#### Scenario: Task created without pipeline (legacy)
- **WHEN** a user creates a task without a `pipeline_def` field
- **THEN** the task SHALL be created with `pipeline_def = NULL` and execute via the legacy path

### Requirement: Phase execution ordering
Each phase in a pipeline definition SHALL declare an execution mode. Phases SHALL execute in strict sequential order as defined in the `phases` array.

#### Scenario: Serial phases
- **WHEN** a pipeline defines phases [prepare, execute, post_process]
- **THEN** the agent SHALL NOT start phase "execute" until all steps in phase "prepare" have completed (or been handled by their `on_failure` policy)

### Requirement: Intra-phase parallelism
Steps within a single phase SHALL support optional parallel execution when the phase declares `parallel: true`.

#### Scenario: Parallel steps within a phase
- **WHEN** a phase declares `parallel: true` and contains 3 steps
- **THEN** the agent SHALL start all 3 steps concurrently using a thread pool, and the phase SHALL complete when all 3 steps have finished

#### Scenario: Serial steps within a phase (default)
- **WHEN** a phase does not declare `parallel` or declares `parallel: false`
- **THEN** the agent SHALL execute steps sequentially in array order

### Requirement: Step failure policy
Each step SHALL declare an `on_failure` policy that determines behavior when the step fails.

#### Scenario: on_failure=stop (default)
- **WHEN** a step fails with `on_failure: "stop"` (or no `on_failure` declared)
- **THEN** the agent SHALL cancel all remaining steps in the current phase and all subsequent phases, and mark the TaskRun as FAILED

#### Scenario: on_failure=continue
- **WHEN** a step fails with `on_failure: "continue"`
- **THEN** the agent SHALL log the failure, mark the step as FAILED, and proceed to the next step or phase

#### Scenario: on_failure=retry
- **WHEN** a step fails with `on_failure: "retry"` and `max_retries: 3`
- **THEN** the agent SHALL retry the step up to 3 times with a 5-second delay between attempts. If all retries fail, the step SHALL be treated as `on_failure: "stop"`

### Requirement: Step timeout enforcement
Each step SHALL declare an optional `timeout` in seconds. The agent SHALL terminate the step if it exceeds the timeout.

#### Scenario: Step exceeds timeout
- **WHEN** a step runs longer than its declared `timeout` value
- **THEN** the agent SHALL terminate the step's execution, mark it as FAILED with error_message "Step timed out after {timeout}s", and apply the step's `on_failure` policy
