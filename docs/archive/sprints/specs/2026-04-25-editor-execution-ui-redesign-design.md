# Editor and Execution UI Redesign Design

## Goal

Refactor the workflow authoring and execution monitoring experience so that use case step authoring, step orchestration, and test execution are clearly separated and operable without hidden behavior.

## Current Problems

- Step authoring is slow: simple fields such as `step_id`, `timeout_seconds`, and `retry` require opening the full editor drawer.
- Step ordering is array-position based, but the UI does not expose direct reordering.
- Test debugging lacks temporary skip controls; users must delete steps to bypass them.
- `WorkflowDefinitionEditPage` only edits `task_templates[0]` and saves a hard-coded `default` template, despite the backend supporting multiple `TaskTemplate` records.
- The full resolved execution flow is implicit across three editors: setup, task, and teardown.
- Dispatch is a blind operation: users cannot preview final steps or override parameters before starting.
- Execution monitoring has correctness issues and dense views: artifact URLs are hand-built incorrectly in places, phase grouping order is unstable, log display can be improved, and the matrix drawer is too narrow for diagnosis.

## Confirmed Scope

This work may include frontend and necessary backend changes. The backend changes are limited to persistence, validation, preview, dispatch override support, and execution behavior needed to make the UI state real.

This work does not redesign the global navigation, authentication, database topology, agent deployment, or unrelated monitoring pages.

## UX Structure

The UI is split into three mental workspaces:

1. Use Case Step Authoring
   - A pipeline editor focused on editing steps inside one pipeline.
   - Step cards support reorder, duplicate, enable/disable, delete, and inline editing of simple fields.
   - The side drawer remains for complex edits: action type, action selection, version, and params.

2. Step Orchestration
   - `WorkflowDefinitionEditPage` manages setup pipeline, multiple task templates, and teardown pipeline as separate sections.
   - A compact top timeline shows the resolved order for the selected task template:
     `setup.prepare -> task.prepare -> task.execute -> task.post_process -> teardown.post_process`.
   - Multiple task templates can be added, renamed, duplicated, deleted, and reordered.

3. Test Execution
   - Starting a workflow opens a dispatch preview instead of immediately launching.
   - Preview shows selected devices, task templates, final resolved pipeline stages, disabled steps, and parameter overrides.
   - Execution matrix supports device search, wider job details, bulk operations where backend support exists, and clearer log/artifact access.

## Data Model and Backend Behavior

### Pipeline Step Enabled State

`PipelineStep` gains an optional `enabled` boolean. Missing `enabled` means `true` for backward compatibility.

Validation accepts:

```json
{
  "step_id": "push_resources",
  "action": "builtin:push_resources",
  "timeout_seconds": 300,
  "retry": 0,
  "enabled": true,
  "params": {}
}
```

Dispatcher and agent execution must skip disabled steps consistently. Disabled steps should appear in preview as disabled and should not be executed. If step traces are emitted for disabled steps, their status must be `SKIPPED`; otherwise preview must clearly distinguish design-time disabled steps from runtime skipped steps.

### Multiple Task Templates

The frontend must preserve all `task_templates` returned by the API. Saving a workflow sends the complete ordered list. Existing backend update semantics already replace/update templates by name, so the frontend must avoid accidental name collisions and keep `sort_order` stable.

### Dispatch Preview and Overrides

Backend adds a preview path that reuses the same pipeline resolution logic as dispatch:

- setup prepare is prepended to task prepare.
- task execute remains task execute.
- teardown post process is appended after task post process.
- disabled steps are included in preview metadata but excluded from executable step count.
- parameter overrides are applied before validation and preview output.

Dispatch uses the same resolution helper as preview to prevent preview/execution drift.

## Frontend Components

### `StagesPipelineEditor`

Responsibilities:

- Edit one `PipelineDef`.
- Render allowed stages only.
- Provide drag sorting using existing `@dnd-kit` dependencies.
- Provide up/down movement buttons as keyboard and low-friction fallback.
- Provide inline inputs for `step_id`, `timeout_seconds`, and `retry`.
- Provide duplicate, enable/disable, edit, and delete controls.
- Keep drawer state local and emit full pipeline updates through `onChange`.

The editor should remain dense and operational, using existing Tailwind and lucide icon patterns. Avoid decorative layouts, nested cards, and hover transforms that shift layout.

### `WorkflowDefinitionEditPage`

Responsibilities:

- Maintain independent state for:
  - basic workflow fields
  - setup pipeline
  - task template list
  - selected task template
  - teardown pipeline
- Render a top execution timeline for the selected task template.
- Save all task templates, not only the first.
- Show unsaved-change detection across all task templates.

### Execution Pages

`WorkflowRunMatrixPage` fixes correctness issues first:

- Artifact download links must use API helpers and correct `runId`/`jobId` semantics.
- Job drawer width should use responsive width, e.g. `w-full max-w-5xl`, so logs and artifacts are usable.
- Device search filters the matrix without changing backend data.
- Bulk actions are exposed only when backend endpoints exist or are added in this work.

`PipelineStepTree` uses fixed phase order:

```ts
const PHASE_ORDER = ['prepare', 'execute', 'post_process'];
```

Unknown phases appear after known phases in lexical order.

`LogsPage` keeps virtual scrolling and adds line numbers and keyword highlighting without using `dangerouslySetInnerHTML`.

`XTerminal` sanitizes incoming log strings before writing to xterm. ANSI color produced by the application may be kept, but untrusted OSC/control sequences from logs must be stripped or escaped.

## Error Handling

- Saving duplicate task template names blocks with a visible validation error.
- Invalid inline numeric fields are clamped or rejected locally before save:
  - `timeout_seconds >= 1`
  - `0 <= retry <= 10`
- Disabled steps are preserved in saved workflow definitions.
- Dispatch preview failure shows backend validation errors and does not start the run.
- If bulk terminate/retry partially fails, the UI reports per-job failures.

## Testing Strategy

Frontend tests:

- `StagesPipelineEditor` supports inline edits, duplicate, enable/disable, up/down reorder, and drag reorder.
- `WorkflowDefinitionEditPage` loads, edits, and saves multiple task templates.
- Execution timeline renders setup/task/teardown in correct order.
- `PipelineStepTree` renders phases in stable order.
- Matrix artifact links use the correct API helper.
- Logs render line numbers and highlight keywords without raw HTML injection.

Backend tests:

- Pipeline schema accepts `enabled`.
- Disabled steps are skipped during execution or resolution.
- Dispatch preview matches dispatch resolution.
- Parameter overrides apply before validation.
- Multi-template dispatch still creates one job per device/template.

Manual verification:

- Create a workflow with setup, two task templates, and teardown.
- Disable one step and confirm it is not executed.
- Reorder and duplicate steps, save, reload, and confirm order/state persists.
- Preview dispatch, apply parameter override, dispatch, and confirm resulting jobs match preview.
- Open execution matrix with multiple devices, filter by serial, inspect logs/artifacts, and download an artifact.

## Implementation Order

1. Fix execution correctness bugs: artifact URL helper, stable phase ordering, terminal/log sanitization.
2. Add `enabled` support through schema, types, validator, and execution behavior.
3. Refactor `StagesPipelineEditor` interactions.
4. Add multi-template state and timeline to `WorkflowDefinitionEditPage`.
5. Add dispatch preview and parameter override support.
6. Improve matrix and logs usability.

## Open Decisions

- Bulk retry semantics should be conservative: retry failed jobs by creating new jobs only if the backend already supports or can safely add that operation. Otherwise the UI should expose bulk terminate first and defer retry.
- Disabled-step runtime traces are optional. The minimum requirement is that disabled steps are persisted, shown in preview, and not executed.

