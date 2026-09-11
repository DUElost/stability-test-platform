# PlanRun O(1) counter lock + reconciler aggregation (#789)

Status: implemented
Class: bug-fix

## Decision

Terminalization/abort/risk-notify paths used `with_for_update(key_share=True)` (PG FOR KEY
SHARE), which does not serialize concurrent counter bumps. Switch to
`with_for_update(read=True)` (PG FOR NO KEY UPDATE) per existing docstring intent.

`counter_reconciler` now calls `apply_plan_run_aggregation_from_counters` after
repairing drift so stuck RUNNING runs can heal.

## Alternatives

- Atomic `UPDATE ... SET terminal_job_count = terminal_job_count + 1` — viable but
  wider refactor; lock fix matches ADR-0026 deadlock design.

## Verification

- `pytest backend/tests/services/test_job_terminalization.py backend/tests/scheduler/test_counter_reconciler_aggregation.py`
- `pytest backend/tests/services/test_aggregator_deadlock_regression.py` (PG integration)

## Revisit

If counter bump moves to SQL increments, drop row lock but keep reconciler aggregation hook.
