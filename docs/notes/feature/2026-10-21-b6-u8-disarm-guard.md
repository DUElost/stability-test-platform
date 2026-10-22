# B6 U8: bounded disarm guard and result policy

Status: proposed
Class: feature

## Decision

Refs #3752; Refs #3723 v1.7.13; ADR-0061 Accepted v1.1.
Original base: `7f4159244f128965325c4964480209729bf5fa2a`.
Resumed from remote `afa85f48f7e3cc2d068ab6d6589b7553d789dbc7`, preserving
Cursor Agent's `033fb566d` and both main merges. This continuation starts at
`57abadf5bc0468787528d1037e10237c38b61185`, retaining the audit guard fixes.
PR #3767 stays draft.

Implement the approved queue/execution guard, platform interruption precedence,
and shared probe trust policy. The nullable JSONB guard is separate from the test
conclusion. LocalDB persists sequences and server deadlines. Permit acquisition
does not wait for phase acknowledgement. Per v1.7.13, each disarm step first
performs U1's single-shot extend_lock check (5-second timeout, no retry). A 409
records not_executed / lease_lost and publishes no execute evidence. An unreachable
control plane delays startup by at most that one check. The tail does not add a
second probe before the first step. Coordinator delivery is capped
at 30 seconds while any job reports a disarm phase; lease renewal alone cannot
establish phase freshness. Reaper, the three recycler final writes and the read
projection use the same cutoff policy. The three final recycler writes and reaper
reread committed facts. Per v1.7.13, the projection uses the request's already-read
job snapshot; only RUNNING jobs with a guard need ownership queries. The projection
cannot mutate the guard, including on a clock regression. Final recycler checks use the post-lock clock
rather than mistaking collection time for a server clock regression.

The Planner resolved the scope return in #3723/6331130472: diagnostic counts use
existing audit rows, not metrics.py. Each rejected/replaced specialty records a
fixed action, entry point, reported/replacement value, reason and report references.
The existing audit writer has async and sync adapters for the respective callers.
Independent diagnostic transactions survive business rejection; identical read-only
replays add no row. v1.7.13 endorses the BUSINESS_ACTIONS_ALLOWLIST registration of
unverified_probe_result with 90-day retention, like device_return_override: these
are diagnostic evidence, not account or credential security events. It also endorses
this action's single _GENERIC_ACTIONS registration: complete/late anchor job_instance
and discharge/init anchor device, with matching resource_id. These are different
entities rather than aliases for the same entity. Read filters expand historical
resource-type aliases; writers use canonical device/job_instance literals. The
content of Cursor's separate audit-guard Note is consolidated here. Discharge reports under distinct lease identities remain distinct.

Register exactly the four MTK / MLD_LX2 or MLD_LX3 / root or non-root combinations
approved in #3730/6331107188. Each carries its APK, script/version, frozen package
SHA and all three reports required by §3.8. Missing or mismatching facts cannot
confirm a return. Raw script output is recomputed on both Agent and control plane.
Old Agent execution retains v1.0 rules without fabricated queue protection; an old
control plane discovered before, during, or while restoring a tail causes capability
warning and v1.0 fallback. U4 owns the actual init endpoint; U8 supplies shared
policy and init service behavior only.

## Alternatives

The implementation uses existing states and the coordinator channel. A script's
self-reported verified flag cannot authorize confirmed. The earlier diagnostic
Prometheus option was rejected by the Planner; no metrics change is needed.
A uniform device resource type would mismatch the job ids used by complete/late;
the approved per-entry entity anchors preserve that relationship. The single action
keeps aggregation consistent across all four entries. No further guard exemptions
are introduced. No budget, business state, environment setting or stage-B switch is added.

## Verification

All pytest commands use the project Python and memory runner. Agent commands use
`env -i PATH="$PATH" PYTHONPATH=.`. Backend commands additionally set only
`TESTING=1 JWT_SECRET_KEY=b6-isolated-test-only`; testcontainers supply isolated PG.
No production environment file is loaded. Device calls and process signals in the
new runtime tests are stubs. No #3739 dangerous mutation is selected.

Iteration evidence before the final-HEAD run:

- v1.7.13 counterexamples before repair: 4 Agent failures and 3 projection/API
  failures (exit 1). Agent/U1 after repair: 139 passed (exit 0); API/phase after
  repair: 96 passed (exit 0). The real devices endpoint returns 21 rows with the
  same query count as one row, including RUNNING and completed jobs without guards.
  The unreachable
  case uses a finite virtual 5-second advance, not a real timeout or host signal.
  Precommit check:quick for v1.7.13: 16 gates passed (exit 0).
- Original priority counterexamples: 18 failed (exit 1), then 18 passed (exit 0).
- Resumption registration/compatibility counterexamples: 5 failed, 24 passed
  (exit 1); four-entry audit counterexamples: 4 failed (exit 1).
- Latest Agent U8 target: 100 passed (exit 0), including local E exhaustion,
  job cap, retries, restart, 11 real scheduler waiters, M1/M2 and durable outbox.
- PG API/phase plus the six existing U2/U3/U5 regression files: 133 passed
  (exit 0), before two additional report-identity/restart counterexamples.
  Those additional counterexamples also failed before their fixes (exit 1).
- Concurrency tests use independent transactions. Candidate→queue/execute,
  terminal and abort changes, ownership failure and actual expiry are covered.
  Eleven same-host jobs advance a bounded virtual clock beyond 61 to 6600 seconds
  through real coordinator/PG/reaper paths, then obtain a single scheduler permit
  in turn and retain 660 seconds per step. No long real sleep is used.
- For each registered combination, the unchanged 1.0.8 script runs with stubbed
  device I/O; its output flows through the real Engine, complete API and PG.
  Each of six identity/boot mismatches is challenged separately on both sides.
- Precommit PG API/phase, six existing regression files and abort reaper:
  160 passed (exit 0). Precommit check:quick: 16 gates passed (exit 0).
- Earlier full-root failures remain historical failures, not acceptance:
  Agent 2, backend 7, root 3. Fixes passed their targeted regressions.

Safe withdrawal command:

```sh
env -i PATH="$PATH" PYTHONPATH=. ./scripts/project_python.sh backend/tests/disarm_withdrawals_3752.py
```

The finite list in that helper restores source bytes in `finally`, then reruns the
same selected tests. Each accepted withdrawal requires pytest exit 1 with a failed
test; each restoration requires exit 0. It never selects cancel/process-group tests.
The previous 34-point matrix exited 0: every withdrawal exited 1 and every restored
run exited 0; source hashes matched after restoration. v1.7.13 replaces the old
projection-reread mutation with request-snapshot/query-count/read-only mutations,
and adds lease-probe, execute-evidence ordering and single-timeout mutations.
The full 39-point matrix rerun for this continuation exited 0: all 39 withdrawals
exited 1, all 39 restored runs exited 0, and all restored source hashes matched.
Final-HEAD command and CI results are reported on PR #3767. Logs and exact child commands for v1.7.13 are under
`/tmp/b6-u8-withdrawals-v1713/`; final matrix
results and the single final-HEAD command/exit record are reported on PR #3767.

Existing-test changes (no test removed, lock/source assertions retained):

- `test_agent_completion_replay_u5_3730.py`: add exact synthetic evidence to the
  legitimate same-source late confirmation; retain replay and token assertions.
- `test_device_return_lock_order_3728.py`: pass synthetic evidence through the
  existing completion/discharge workers; retain real concurrency/lock assertions.
- `test_device_return_u3_3728.py`: §3.8 replaces unverified discharge input with
  trusted confirmed in the positive partial-discharge case. Add counterexamples
  proving executed_unverified and evidence-free confirmed leave debt and lease open.
- `test_disarm_results_u2_3727.py`: §3.0.6 changes STARTED-only to incomplete;
  add legacy 124/125 counterexamples, retaining the existing no-start and
  late-STARTED-after-COMPLETED contrasts.
- `test_planner_u3_contract_probes.py`: add exact synthetic evidence to legitimate
  same-source closure; preserve source, terminal-status and lease assertions.
- `test_reviewer_u3_r1_f1_f2_3728.py`: §3.8 replaces unverified discharge input
  with trusted confirmed; assert returned for that path while override stays
  unverified. Keep zero-open-debt, lease-release and stale-session checks.
- `test_powercycle_disarm_u5_3730.py`: §3.8.1 / v1.7.12 supersedes the pending
  empty registry with exact four-combination assertions. U8 adds registration
  withdrawal and each-field mismatch counterexamples to retain the negative side.

Final verification commands (the actual exit statuses and full HEAD are recorded
in PR #3767 after this Note's commit):

```sh
env -i PATH="$PATH" PYTHONPATH=. ./scripts/project_python.sh scripts/run_pytest.py backend/agent/tests/ -q
env -i PATH="$PATH" PYTHONPATH=. TESTING=1 JWT_SECRET_KEY=b6-isolated-test-only ./scripts/project_python.sh scripts/run_pytest.py backend/tests/ -q
env -i PATH="$PATH" PYTHONPATH=. ./scripts/project_python.sh scripts/run_pytest.py tests/ -q
env -i PATH="$PATH" PYTHONPATH=. ./scripts/project_python.sh scripts/run_pytest.py tests/test_disarm_guard_migration_3752.py -q
./scripts/project_python.sh tools/dev/check_script_packages.py
./scripts/project_python.sh tools/dev/check_tool_manifest.py --base origin/main
env -i PATH="$PATH" PYTHONPATH=. ./scripts/project_python.sh scripts/run_gates.py check:quick
```

Required CI: lint, CodeQL, pr-typecheck, pr-compileall, pr-agent-tests,
pr-migrate-empty-db; also obtain pr-backend-tests-informational on the same HEAD.
CI on the earlier remote HEAD does not validate this change's final HEAD.

## Revisit

Planner verification and independent review remain required. Keep PR #3767 draft.
U4 must reuse the shared init policy and verify its real token-protected entry.
Merge does not activate the change: no production migration, initialization,
deployment, device operation, publication, scan, hot update or plan repointing is
part of this implementation.
