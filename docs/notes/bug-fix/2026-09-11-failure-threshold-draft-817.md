# Plan failure threshold decimal input (#817)

Status: implemented
Class: bug-fix

## Decision

`PlanCanvas` failure threshold uses a local draft input (`FailureThresholdInput`):
keystrokes update draft only; blur/Enter parses, clamps to [0, 1], then commits.
Prevents `0.` being swallowed by immediate `parseFloat` + controlled value writeback.

## Alternatives

- 0–100 integer percent input — rejected; keep 0–1 fractional semantics.

## Verification

- `npm run test -- --run src/components/pipeline/PlanCanvas.test.tsx`

## Revisit

Extract shared decimal draft input if more Plan meta fields need the same pattern.
