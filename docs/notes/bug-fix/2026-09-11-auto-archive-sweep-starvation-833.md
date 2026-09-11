# auto_archive_sweep oldest-due terminal selection (#833)

Status: implemented
Class: bug-fix

## Decision

When no RUNNING PlanRun exists, `auto_archive_sweep` now selects the **oldest**
terminal run whose `ended_at + interval` has elapsed and archive is incomplete,
instead of always picking the newest terminal run. One run per plan per sweep
still holds; RUNNING incremental path unchanged.

## Alternatives

- Process all due runs in one sweep — rejected; risks scan burst and duplicates
  existing per-plan throttle intent.
- Keep newest-first and document starvation — rejected; matches user-visible bug.

## Verification

- `pytest backend/agent/tests/test_saq_scan_pipeline.py -k auto_archive_sweep` (10 passed)

## Revisit

If merge latency grows, consider bounded multi-run sweep per plan (e.g. cap at 3).
