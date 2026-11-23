# Frontend UI Governance Wave 4 U4-1c: PlanEditPage & PlanListPage

Status: implemented
Class: feature

## Decision

Land Wave 4 `U4-1c` (`#4117`) covering `PlanEditPage` and `PlanListPage`:

1. **`PlanStepInspector` script picker (`D1`)**:
   - Extend `SearchableSelect` with optional `disabled` and `triggerClassName` props so read-only inspectors disable selection cleanly without breaking existing filter usages.
   - Replace the native `<select>` for `脚本名` in `PlanStepInspector` with `SearchableSelect` (searchable, Portal-rendered, preserving unknown/inactive script fallback labels).
2. **`PlanEditPage` draft projection debounce & canvas vertical space**:
   - Debounce `api.plans.draftParameterProjection` calls by `200ms` in `usePlanEditForm` while keeping saved `parameterProjection` queries immediate, eliminating keystroke request bursts during editing.
   - Add `defaultCollapsed` and `hideTimingInputs` props to `PlanSettingsEditor` (defaulting to `false` to preserve standalone usages) and pass them from `PlanCanvas` so the editor opens with the settings drawer collapsed to a single summary bar and does not duplicate `patrol_interval_seconds` / `timeout_seconds` already edited in the canvas header.
3. **`PlanListPage` URL persistence & header convergence (`D4`)**:
   - Persist `?q=`, `?project=`, and `?specialty=` via `useUrlFilterParams` for deep-link sharing and reload survival.
   - Align `PlanListPage` on `PageContainer width="content"` and place the primary `新建 Plan` CTA in `PageHeader.action`.

## Alternatives

- Kept `PlanSettingsEditor` default expanded on `PlanCanvas`: rejected because the top settings form consumes ~200px above the pipeline graph and duplicates timing inputs already visible on the canvas bar.
- Left `脚本名` as native `<select>`: rejected under UI governance contract `D1` because active script catalogs grow dynamically and require keyword filtering.

## Verification

- Vitest: `PlanListPage.test.tsx`, `PlanStepInspector.test.tsx`, `PlanSettingsEditor.test.tsx`, `PlanEditPage.test.tsx`, `searchable-select.test.tsx` (and full orchestration/pipeline/parameters tests).
- Frontend typecheck (`npx tsc --noEmit`) and lint (`npx eslint`).
- Local quick gates (`scripts/run_gates.py check:quick`).

## Revisit

- If `PlanEditPage` later moves step/parameter authoring to a dedicated drawer or server-side autosave, revisit the `200ms` draft projection debounce window.
