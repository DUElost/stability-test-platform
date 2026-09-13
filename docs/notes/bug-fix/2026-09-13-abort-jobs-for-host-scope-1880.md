# abort_jobs_for_host host scope (#1880)

Status: implemented
Class: bug-fix

## Decision

Add optional `host_id` to `abort_plan_run()` so `abort_jobs_for_host()` aborts
only jobs on the target host. Other hosts on the same PlanRun stay RUNNING;
`run_context.abort_requested.requested_job_ids` merges with in-flight partial
aborts instead of replacing them. Host-scoped calls skip whole-plan QUEUED/
PRECHECK/in-precheck FAILED paths.

## Alternatives

- Keep calling whole-plan `abort_plan_run()` and filter in
  `abort_jobs_for_host()` only at the query layer — rejected: still aborted all
  jobs once inside the service.
- New `abort_plan_run_jobs_for_host()` entry point — rejected: duplicates lock,
  audit, and emit paths.

## Verification

- `test_abort_jobs_for_host_scoped_to_host_only` in
  `backend/tests/api/test_plan_run_abort_api.py`
- `pytest backend/tests/api/test_plan_run_abort_api.py -q`

## Revisit

- If multi-host partial abort becomes a first-class API, document merge semantics
  for `acknowledged_job_ids` and aggregation edge cases.
