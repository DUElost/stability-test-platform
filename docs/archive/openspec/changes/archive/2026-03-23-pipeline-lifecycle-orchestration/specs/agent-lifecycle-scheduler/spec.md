## ADDED Requirements

### Requirement: Lifecycle pipeline definition format
The system SHALL support a `lifecycle_def` structure within `pipeline_def` that defines a multi-phase test lifecycle consisting of init, patrol, and teardown pipelines.

#### Scenario: Lifecycle pipeline_def structure
- **WHEN** a pipeline_def contains a `lifecycle` key
- **THEN** the structure SHALL follow this format:
  ```json
  {
    "lifecycle": {
      "init": { "stages": { ... } },
      "patrol": { "stages": { ... }, "interval_seconds": 300 },
      "teardown": { "stages": { ... } }
    }
  }
  ```
  where `init`, `patrol`, and `teardown` each contain a standard stages-format pipeline_def, and `patrol` additionally specifies `interval_seconds`

#### Scenario: Lifecycle with timeout
- **WHEN** a lifecycle_def includes `"timeout_seconds": 86400`
- **THEN** the lifecycle scheduler SHALL trigger teardown after the specified duration has elapsed since init completion, regardless of patrol status

#### Scenario: Non-lifecycle pipeline_def backward compatibility
- **WHEN** a pipeline_def does NOT contain a `lifecycle` key (only has `stages`)
- **THEN** the pipeline engine SHALL execute it as a single pipeline run (existing behavior, no lifecycle scheduling)

### Requirement: Agent lifecycle scheduler execution
The agent SHALL implement a lifecycle scheduler that orchestrates the init → patrol_loop → teardown sequence for lifecycle-type pipelines.

#### Scenario: Normal lifecycle execution flow
- **WHEN** the agent receives a job with a lifecycle pipeline_def
- **THEN** the agent SHALL:
  1. Execute the `init` pipeline stages (prepare → execute → post_process)
  2. On init success, enter patrol loop: execute `patrol` pipeline, wait `interval_seconds`, repeat
  3. On termination trigger (timeout / fatal patrol failure / abort signal), execute `teardown` pipeline
  4. Report job completion after teardown finishes (or fails)

#### Scenario: Init failure skips patrol and runs teardown
- **WHEN** the init pipeline fails
- **THEN** the agent SHALL skip the patrol loop entirely and proceed directly to teardown execution, then report job as FAILED

#### Scenario: Patrol failure triggers teardown
- **WHEN** a patrol pipeline execution fails with a non-recoverable error (exit_code != 0 after retries)
- **THEN** the agent SHALL stop the patrol loop and proceed to teardown, then report job as FAILED with `error_message` indicating which patrol iteration failed

#### Scenario: Timeout triggers teardown
- **WHEN** the lifecycle `timeout_seconds` elapses (measured from init completion)
- **THEN** the agent SHALL stop the patrol loop at the next patrol boundary (do not interrupt a running patrol), execute teardown, and report job as COMPLETED

#### Scenario: Abort signal triggers teardown
- **WHEN** the agent receives an abort signal (lock lost / manual cancel via is_aborted)
- **THEN** the agent SHALL stop the patrol loop at the next check point, execute teardown, and report job as ABORTED

### Requirement: Patrol scheduling semantics
The patrol loop SHALL use fixed-delay scheduling to prevent patrol accumulation.

#### Scenario: Fixed-delay interval
- **WHEN** a patrol execution completes at time T and `interval_seconds` is 300
- **THEN** the next patrol SHALL NOT start until T + 300 seconds (measured from patrol completion, not from patrol start)

#### Scenario: Single patrol instance
- **WHEN** a patrol is currently executing
- **THEN** no additional patrol SHALL be scheduled or started for the same device until the current patrol completes

#### Scenario: Patrol iteration tracking
- **WHEN** each patrol executes
- **THEN** the scheduler SHALL increment an `iteration` counter starting from 1 and include it in step log messages as `[Patrol #N]`

### Requirement: Teardown best-effort execution
The teardown pipeline SHALL execute with best-effort semantics: individual step failures SHALL NOT prevent subsequent steps from executing.

#### Scenario: Teardown step failure does not block subsequent steps
- **WHEN** a teardown step fails (e.g., `collect_bugreport` times out)
- **THEN** the scheduler SHALL log the failure, continue executing the remaining teardown steps (`scan_aee`, `export_mobilelogs`, `aee_extract`, etc.), and report teardown as DEGRADED (not FAILED) if at least some steps succeeded

#### Scenario: Teardown idempotency
- **WHEN** teardown is triggered multiple times (e.g., both timeout and abort fire simultaneously)
- **THEN** the scheduler SHALL execute teardown at most once, using a flag or atomic state transition to prevent duplicate execution

#### Scenario: Teardown with device unreachable
- **WHEN** teardown begins but the device is unreachable (ADB connection lost)
- **THEN** the scheduler SHALL attempt each teardown step, log failures for device-dependent steps, and complete teardown with status DEGRADED

### Requirement: Lifecycle status reporting
The agent SHALL report lifecycle phase transitions and patrol progress via the existing MQ/WS reporting channel.

#### Scenario: Phase transition events
- **WHEN** the lifecycle transitions between phases (init → patrol → teardown)
- **THEN** the agent SHALL emit a `job_status` event with the phase name (e.g., `PATROL_RUNNING`, `TEARDOWN_RUNNING`)

#### Scenario: Patrol progress reporting
- **WHEN** each patrol iteration completes
- **THEN** the agent SHALL include `iteration`, `next_patrol_at`, and `time_remaining` in the status event metadata

#### Scenario: Termination reason reporting
- **WHEN** teardown is triggered
- **THEN** the agent SHALL include `termination_reason` in the job completion event, with values: `timeout`, `patrol_failure`, `abort`, `init_failure`, or `manual_cancel`
