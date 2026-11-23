# Frontend UI Governance Wave 4 U4-3: 9 Frozen Pages Minimal Stopgaps & D6 AI Closure

Status: implemented
Class: feature

## Decision

Land Wave 4 `U4-3` (`#4117`) strictly within the 9-page freeze boundary (targeted minimal stopgaps only; no page redesign or backend contract changes):

1. **AI Assistant D6 Closure (`AssistantPage`, `AssistantApprovalsPage`, `ActionCard`, `Sidebar`, `SettingsPage`, `AiAssistantSettingsPage`)**:
   - Extract shared `frontend/src/pages/assistant/toolLabels.ts` covering all 12 backend T2 tool names (`abort_plan_run`, `retry_plan_run_dispatch`, `manual_retry_job`, `manual_exit_job`, `trigger_plan_run_archive` added alongside the original 7), used by both `AssistantApprovalsPage` and `ActionCard`.
   - `AssistantPage`: read `?prompt=` query parameter to prefill draft input from `DeviceDetailDrawer` / `HostDetailDrawer`, render `InlineError` when `messagesQ.isError` instead of a blank thread, and switch layout to `PageContainer width="wide" scrollable={false}` + `flex-1 min-h-0` to eliminate fixed-height voids and double scrollbars.
   - `Sidebar`: display a live pending-approval count badge on `/assistant` for admins when `QueryClientContext` is present.
   - `SettingsPage` ↔ `AiAssistantSettingsPage`: add mutual navigation links and eliminate the 1-frame error flicker in `AiAssistantSettingsPage` while `form` hydrates from `configQ.data`.
2. **Reporting & Issue Tracker Stopgaps (`TestTypePassFailChart`, `RunReportPage`, `JiraRunHistory`, `IssueTrackerPage`, `JiraSubmitPanel`)**:
   - Disable bar re-animation (`isAnimationActive={false}`) on `TestTypePassFailChart` to stop 30s refetch flicker.
   - Fix invalid `<a><Button>` nesting in `RunReportPage` via `<Button asChild><a ... download>` and wire the breadcrumb back to `/execution/plan-runs/:planRunId` when `?planRun=` is present.
   - Remove conflicting `TEXT.caption` from `JiraRunHistory` error lines and apply `text-destructive-text`.
   - Wire `?planRunId=` from `IssueTrackerPage` into `JiraSubmitPanel` and expand recent PlanRun selection from 10 to 50.
3. **Schedules & File Server Stopgaps (`SchedulesPage`, `FileServerPage`)**:
   - Render Plan names (via `planNameById` lookup, fallback `Plan #id`) in `SchedulesPage` and scope row busy state by `schedule.id` instead of locking all rows.
   - Add frontend host search filter and `/hosts?q=` cross-links in `FileServerPage`'s `AgentStatusSection`.

## Alternatives

- Deeply refactoring `ResultsPage`, `Dashboard`, or `FileServerPage` layouts: explicitly rejected to uphold the Owner-approved 9-page freeze boundary (`[L3]` items remain tracked separately).

## Verification

- Vitest on `Sidebar.test.tsx`, `AssistantPage.test.tsx`, `AssistantApprovalsPage.test.tsx`, `AiAssistantSettingsPage.test.tsx`, `RunReportPage.test.tsx`, `JiraSubmitPanel.test.tsx`, `SchedulesPage.test.tsx`, `FileServerPage.test.tsx`.
- Frontend `tsc --noEmit`, `eslint --max-warnings 0`, and `scripts/run_gates.py check:quick`.

## Revisit

- Revisit frozen-page UX only when their respective L3 product specs (`ResultsPage`, `Dashboard`, `IssueTrackerPage`, etc.) enter Phase 2.
