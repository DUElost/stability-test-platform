# SchedulesPage action in-flight guard (#820)

Status: implemented
Class: bug-fix

## Decision

Add `actionInFlight` ref + `actionBusy` state with `runGuarded` wrapper for save,
run-now, toggle, and delete. Disable action buttons while a mutation is pending
to prevent double-submit duplicates.

## Alternatives

- Per-row busy flags — rejected; global guard matches issue scope and blocks
  cross-action double clicks too.

## Verification

- `npm run test -- --run src/pages/schedules/SchedulesPage.test.tsx`

## Revisit

If row-level parallelism is desired later, narrow guard to per-action keys.
