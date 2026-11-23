# Frontend UI Governance Wave 4 U4-2: Asset & Governance Pages (Zero Remaining Static Guard Allowlists)

Status: implemented
Class: feature

## Decision

Land Wave 4 `U4-2` (`#4117`) across Asset & Governance pages (`TestSuitesPage`, `TestSuiteDetailPage`, `ProjectsPage`, `ProjectDetailPage`, `ScriptManagementPage`, `AuditLogPage`, `NotificationsPage`, `WifiPage`), bringing every shrink-only static UI guard allowlist to zero:

1. **Test Suites (`TestSuiteDetailPage`, `CaseEditDialog`, `TestSuitesPage`)**:
   - Migrate `TestSuiteDetailPage` header to `PageHeader` with breadcrumbs, converge 8 header action buttons into 4 primary actions + `DropdownMenu`「更多」, migrate raw `<table>` to `@/components/ui/table`, replace both `window.confirm` calls with `useConfirm()`/`AlertDialog`, and debounce `caseSearch` by `250ms`.
   - Fix `CaseEditDialog` `dialogKey` to include `isOpen` so reopening "新增用例" always resets stale inputs.
   - Add `250ms` search debounce and URL persistence (`?q=`, `?project=`) via `useUrlFilterParams` on `TestSuitesPage`.
2. **Projects (`ProjectsPage`, `ProjectDetailPage`, `MapModelsDialog`, `EditProjectDialog`)**:
   - Replace all 4 `window.confirm` calls across `ProjectsPage` and `ProjectDetailPage` with `useConfirm()`/`AlertDialog`.
   - Gate SEED queue `转为项目` / `放弃` actions in `ProjectsPage` behind `isAdmin`.
   - Append `?project=` deep links to `ProjectDetailPage` `TruncationNote` links (`/devices?project=...`, `/orchestration/plans?project=...`).
   - Replace 3 hardcoded `text-amber-700` palette classes in `MapModelsDialog` with semantic `text-warning`.
   - Fix contradictory `EditProjectDialog` description copy when `canRename` is enabled.
3. **Scripts (`ScriptManagementPage`, `ScriptVersionDialog`, `#3130`)**:
   - Gate `扫描脚本目录` and `新建版本` actions behind `isAdmin` via `useAuthSession`.
   - Add `submitting` state lock to `ScriptVersionDialog` to prevent double submission.
4. **Governance & Resources (`AuditLogPage`, `NotificationsPage`, `WifiPage`)**:
   - Add one-click `重置筛选` action on `AuditLogPage` when any filter is active.
   - Apply `LAYOUT.pageGap` on `NotificationsPage`, sync tab changes back to URL `?tab=`, and switch manual pagination buttons in `NotificationLogsTab` to `PaginationBar`.
   - Fix `WifiPage` error+empty co-render so failed queries render `InlineError` exclusively instead of also showing `暂无 WiFi 资源池`.
5. **Static Guard Allowlists (`tests/test_frontend_filter_workbench_guard.py`)**:
   - `_NATIVE_DIALOG_ALLOWLIST`: `3 → 0`
   - `_RAW_TABLE_ALLOWLIST`: `→ 0`
   - `_HARDCODED_PALETTE_ALLOWLIST`: `1 → 0`
   - Combined with `U1-1` (`_BESPOKE_ARIA_MODAL_ALLOWLIST = 0`) and `U4-1ab` (`_CLICKABLE_TR_ALLOWLIST = 0`, `_MISSING_PAGE_CONTAINER_ALLOWLIST = 0`), **all 6 UI governance static guard allowlists reach `0`**.

## Alternatives

- Leaving `window.confirm` in `ProjectsPage`/`ProjectDetailPage`/`TestSuiteDetailPage`: rejected under UI governance contract D2/A11y and static guard allowlist zeroing target.
- Keeping all 8 admin buttons inline on `TestSuiteDetailPage`: rejected because the action row wraps heavily at `1280×800`; secondary export/deactivate actions belong in a `DropdownMenu`.

## Verification

- `pytest tests/test_frontend_filter_workbench_guard.py -v`: all 8 guards green with zero allowlist entries remaining.
- Vitest across `src/pages/suites/`, `src/pages/projects/`, `src/pages/scripts/`, `src/pages/audit/`, `src/pages/notifications/`, `src/pages/wifi/`.
- Frontend `tsc --noEmit`, `eslint --max-warnings 0`, and `scripts/run_gates.py check:quick`.

## Revisit

- If `ScriptManagementPage` active version counts grow beyond ~100 rows, revisit collapsible family grouping in Phase 2.
