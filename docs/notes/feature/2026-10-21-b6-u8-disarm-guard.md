# B6 U8: bounded disarm guard and result policy

## Decision

Refs #3752; Refs #3723 v1.7.11; ADR-0061 Accepted v1.1.
Base: `7f4159244f128965325c4964480209729bf5fa2a`.
This draft implements the approved queue/execution guard, platform interruption
precedence, and shared probe trust policy. The nullable JSONB guard is separate
from the test conclusion. Agent state persists sequences and server deadlines.

This is work in progress, not an acceptance or activation report. Real trusted
combinations remain empty pending Planner confirmation of #3730 §3.6.8.

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
- Actual PG recovery, independent-transaction races, Engine integration,
  reversible mutations, migration up/down/up, three complete test roots,
  `check:quick`, script package checks and required CI: pending.

## Revisit

Complete the finite acceptance matrix in #3723 §4.1/§4.2 on the final HEAD.
Planner confirmation precedes real combination registration; U4 owns the real
init wiring. Keep this PR draft for Planner check and independent review. No
production migration, deployment, initialization, device action or publication
is part of this implementation.
