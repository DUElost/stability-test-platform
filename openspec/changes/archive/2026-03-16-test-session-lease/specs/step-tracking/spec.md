## ADDED Requirements

### Requirement: Reconciler session-aware recovery
The reconciler SHALL reconstruct job state from StepTrace records after an Agent reconnects from an `UNKNOWN` state. The relationship between StepTrace and the device session is implicit: `StepTrace.job_id` links to `JobInstance.id`, and `Device.lock_run_id` identifies the active session holder.

#### Scenario: Agent reconnects with partial step traces
- **WHEN** a job transitions from `UNKNOWN` back to `RUNNING` (Agent recovery) and the reconciler replays buffered StepTrace records
- **THEN** the reconciler SHALL use `StepTrace.job_id` to match traces to the recovered job and update step statuses accordingly, without requiring a separate session identifier

#### Scenario: Reconciler encounters traces for expired session
- **WHEN** the reconciler receives StepTrace records for a job whose device lock has been released (session expired)
- **THEN** the reconciler SHALL still persist the traces (idempotent upsert) but SHALL NOT transition the job out of its terminal state (`FAILED`/`COMPLETED`)

### Requirement: StepTrace timestamps for session forensics
StepTrace records SHALL include the `original_ts` field (already defined) to enable post-hoc session timeline reconstruction. When the watchdog transitions a job to `UNKNOWN` or `FAILED`, the timestamp of that transition SHALL be available for comparison against StepTrace timestamps.

#### Scenario: Determine last activity before session timeout
- **WHEN** a job is transitioned to `FAILED` by the watchdog after UNKNOWN grace period expiry
- **THEN** the most recent StepTrace `original_ts` for that job SHALL indicate the last known activity time, enabling operators to determine whether the Agent crashed mid-step or between steps
