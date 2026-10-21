# B6 U8: bounded disarm guard and result policy

Status: proposed
Class: feature

## Decision

Refs #3752; Refs #3723 v1.7.11; ADR-0061 Accepted v1.1.
Base: `7f4159244f128965325c4964480209729bf5fa2a`.
This draft implements the approved queue/execution guard, platform interruption
precedence, and shared probe trust policy. The nullable JSONB guard is separate
from the test conclusion. Agent state persists sequences and server deadlines.

This is work in progress, not an acceptance or activation report. Real trusted
combinations remain empty pending Planner confirmation of #3730 §3.6.8.

Implementation returned to the Planner under #3723 §3.1/§7: §3.8 requires
independent counting of rejected/replaced probe evidence, but U8's file list
excludes `backend/core/metrics.py`. The existing `stp_disarm_result_total` is a
six-outcome counter and cannot carry a seventh diagnostic outcome. The draft
keeps the independent audit and leaves diagnostic counter wiring pending.
No file outside the declared U8 list was changed for this gap.

## Alternatives

The implementation uses the existing execution states and coordinator channel.
Lease renewal alone cannot establish phase freshness. Script-reported verification
flags cannot authorize a confirmed result. No budget, state, environment variable,
or stage-B activation switch is added.

## Verification

- Clean Agent environment, project memory runner: first platform-priority
  counterexamples failed (18 failures, exit 1), then passed (18 passed, exit 0).
- Clean Agent environment, targeted U1/U5 and new counterexamples: 64 passed,
  exit 0. Expanded pure policy/guard counterexamples: 42 passed, exit 0.
- Changed Python files checked with project `-m ruff check`: exit 0.
- Clean Agent runner, latest targeted U8 file: 62 passed, exit 0, including
  11 real scheduler waiters / one permit at virtual 61s, 6600s and 7201s,
  immediate permit-to-disarm under unavailable control plane, durable outbox,
  LocalDB restart, old control plane, and 60s configuration / 30s delivery.
- Isolated PG runner, new real API and phase files: 34 passed, exit 0.
  Includes reaper→extend at abort+61s, stale ORM candidates at all three final
  recycler entries, actual expiry selection, ownership failures, complete,
  same-source late reports, partial discharge, and Engine→runner→complete→debt.
- A prior new API run reached 23 passing test bodies but failed during cleanup
  (exit 1): the injected sync Session retained locks after a rejected request.
  The test fixture now rolls back before independent cleanup; the 34-test
  run above includes the corrected fixture. This failed run is not acceptance.
- Existing U1/U5 + lifecycle targeted run: 71 passed, exit 0. Existing U2/U3/U5
  transaction/source/replay regressions after explicit test evidence updates:
  50 passed, exit 0. STARTED without a terminal now asserts incomplete.
- `tests/test_disarm_guard_migration_3752.py`: isolated empty PG upgrade head,
  downgrade one revision, upgrade head; nullable JSONB verified; 1 passed,
  exit 0. The graph has one head, `u8b6d3752a01`.
- First full roots during iteration: Agent 2754 passed / 2 failed (exit 1),
  backend 4236 passed / 7 failed / 1 skipped (exit 1), root 2413 passed /
  3 failed / 18 skipped (exit 1). These are not final-HEAD acceptance.
  The affected targeted reruns passed after fixes. Legacy confirmation tests
  now carry explicit test-only device/package/tool evidence; lock/source/
  transaction assertions remain, and discharge no longer closes unverified debt.
- Root inner-import/static-guard targeted rerun: 9 passed, exit 0. New imports
  are module-level; the inner-import baseline remains 610. Runtime PG tests
  use a phase filename rather than the repository's static-guard name marker.
- `scripts/run_gates.py check:quick`: second run passed 16 gates, exit 0,
  including append-only manifest and script-package self-tests; first run
  failed on missing Note metadata (exit 1), corrected in this draft.
- Project Ruff on changed files and `git diff --check`: exit 0.
- Per-fix withdrawal mutations, complete three roots at one final HEAD,
  final-HEAD migration/package/quick reruns and six required CI: pending.

Commands use `./scripts/project_python.sh`; every pytest uses
`scripts/run_pytest.py`. Agent runs use `env -i PATH="$PATH" PYTHONPATH=.`;
backend runs add only `TESTING=1 JWT_SECRET_KEY=b6-isolated-test-only` and let
testcontainers create PG. Iteration logs are `/tmp/b6-u8-*.log` on the execution
host. No dangerous #3739 mutation was run.

## Revisit

Complete the finite acceptance matrix in #3723 §4.1/§4.2 on the final HEAD.
Resume implementation only after the Planner resolves the scope return above.
Planner confirmation precedes real combination registration; U4 owns the real
init wiring. Keep this PR draft for Planner check and independent review. No
production migration, deployment, initialization, device action or publication
is part of this implementation.
