# Frontend UI Governance Wave 4 U4-1ab: Execution Workbench Pages

Status: implemented
Class: feature

## Decision

Land Wave 4 `U4-1a` (`PlanExecutePage`) and `U4-1b` (`PlanRunDetailPage`, `PlanRunLogsPage`) together (`#4117`):

1. **`ClickableRow` ref & HTML attribute forwarding**:
   - Upgrade `ClickableRow` with `forwardRef<HTMLTableRowElement, ClickableRowProps>` and pass-through `HTMLAttributes<HTMLTableRowElement>` so table rows needing refs (`DeviceOverview` focus restoration) and `data-*` attributes (`DeviceTablePanel` row anchors) converge onto the single keyboard-accessible row primitive.
2. **`PlanExecutePage` & `DeviceTablePanel` (`U4-1a`)**:
   - Pre-group `allDevices` by `hostId` in a `Map` so `nodeSummaries` runs in O(D + H) instead of O(H × D).
   - Memoize `baseFilteredDevices` via `useMemo`.
   - Remove internal ADR reference (`（挂 ADR-0026 QUEUED UI）`) from user-facing subtitle.
   - Migrate `DeviceTablePanel` rows to `ClickableRow` and hoist a single `TooltipProvider` outside the row loop instead of creating one per row.
3. **`PlanRunDetailPage`, `DeviceOverview`, `LogEventsCard`, `TestCaseResultsCard`, `PlanRunLogsPage` (`U4-1b`)**:
   - Wrap `PlanRunDetailPage` in `PageContainer width="bleed" scrollable={false}`.
   - Migrate `DeviceOverview` rows to `ClickableRow` (preserving focus restoration ref and keyboard activation).
   - Migrate `LogEventsCard` and `TestCaseResultsCard` from raw `<table>` markup to `@/components/ui/table` while preserving `#1193` slow polling for asynchronous archive artifacts and `#2150` state tooltips.
   - Persist `stage`, `severity`, `q`, and `page` in URL query parameters on `PlanRunLogsPage` via `useUrlFilterParams` while preserving `#2087` non-reset when debounced search text is unchanged.
4. **Static guard allowlist shrinkage (`tests/test_frontend_filter_workbench_guard.py`)**:
   - `_CLICKABLE_TR_ALLOWLIST`: `2 → 0` (zeroed).
   - `_MISSING_PAGE_CONTAINER_ALLOWLIST`: `1 → 0` (zeroed).
   - `_RAW_TABLE_ALLOWLIST`: `3 → 1` (only `TestSuiteDetailPage.tsx` remains for `U4-2`).

## Alternatives

- Splitting `U4-1a` and `U4-1b` into two separate PRs against `tests/test_frontend_filter_workbench_guard.py`: rejected because both PRs belong to the same execution-workbench cluster and would serialize unnecessarily on the same guard allowlist file.
- Disabling `SLOW_REFETCH_MS` on `LogEventsCard`: rejected because `#1193` explicitly requires slow polling on terminal PlanRuns so asynchronous post-processing archive artifacts appear without manual reload.

## Verification

- `pytest tests/test_frontend_filter_workbench_guard.py` (all static guard tests green).
- Vitest on `src/pages/execution/`, `src/components/execution/`, `src/components/plan-run/`, `src/hooks/plan-run/`.
- Frontend `tsc --noEmit`, `eslint --max-warnings 0`, and `scripts/run_gates.py check:quick`.

## Revisit

- If `PlanRunDetailPage` terminal cards grow further in Phase 2, revisit grouping archive/dedup/log-events/test-case-results into sub-tabs once L3 result workflows stabilize.
