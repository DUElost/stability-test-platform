# Editor and Execution UI Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Separate use case step authoring, workflow step orchestration, and test execution into clear UI flows with backend-backed persistence and dispatch behavior.

**Architecture:** Add small backend capabilities first (`enabled`, preview, overrides), then refactor frontend around focused components. Keep preview and dispatch using one shared pipeline resolution path so the UI never previews a different flow than the backend runs.

**Tech Stack:** FastAPI, SQLAlchemy, pytest, React, TypeScript, Tailwind, React Query, Vitest, Testing Library, `@dnd-kit`, lucide-react.

**Repo Rule:** Do not commit unless the user explicitly asks. Use `git -c safe.directory=F:/stability-test-platform status --short` as checkpoints.

---

## File Map

- Modify: `backend/schemas/pipeline_schema.json` — accept `enabled`.
- Modify: `backend/core/pipeline_validator.py` — semantic validation still ignores disabled steps only for execution, not schema.
- Modify: `backend/services/dispatcher.py` — shared preview/dispatch resolution, overrides, disabled-step filtering.
- Modify: `backend/api/routes/orchestration.py` — preview endpoint, trigger schema, preview response schema.
- Modify: `backend/agent/pipeline_engine.py` — skip disabled steps if a persisted job contains them.
- Test: `backend/core/test_pipeline_validator.py`
- Test: `backend/tests/services/test_dispatcher_setup_pipeline.py`
- Test: `backend/agent/tests/test_pipeline_engine_script_action.py`
- Modify: `frontend/src/utils/api/types.ts` — `PipelineStep.enabled`, preview/override types.
- Modify: `frontend/src/utils/api/orchestration.ts` — `previewRun`, corrected artifact helper signature.
- Modify: `frontend/src/components/pipeline/StagesPipelineEditor.tsx` — dnd, inline edit, copy, enable/disable.
- Test: `frontend/src/components/pipeline/StagesPipelineEditor.test.tsx`
- Create: `frontend/src/components/pipeline/PipelineExecutionTimeline.tsx` — compact resolved flow.
- Test: `frontend/src/components/pipeline/PipelineExecutionTimeline.test.tsx`
- Create: `frontend/src/pages/orchestration/workflowTemplateState.ts` — pure helpers for template state.
- Test: `frontend/src/pages/orchestration/workflowTemplateState.test.ts`
- Modify: `frontend/src/pages/orchestration/WorkflowDefinitionEditPage.tsx` — multi-template UI and save.
- Modify: `frontend/src/pages/orchestration/WorkflowDefinitionListPage.tsx` — dispatch preview modal entry.
- Create: `frontend/src/pages/orchestration/DispatchPreviewDialog.tsx` — preview + overrides + launch.
- Test: `frontend/src/pages/orchestration/DispatchPreviewDialog.test.tsx`
- Modify: `frontend/src/components/pipeline/PipelineStepTree.tsx` — stable phase order.
- Test: `frontend/src/components/pipeline/PipelineStepTree.test.tsx`
- Modify: `frontend/src/components/log/XTerminal.tsx` — sanitize untrusted control sequences.
- Test: `frontend/src/components/log/XTerminal.test.tsx`
- Modify: `frontend/src/pages/execution/WorkflowRunMatrixPage.tsx` — artifact helper, wider drawer, search.
- Test: `frontend/src/pages/execution/WorkflowRunMatrixPage.test.tsx`
- Modify: `frontend/src/pages/logs/LogsPage.tsx` — line numbers and keyword highlight without raw HTML.

---

### Task 1: Fix Execution Correctness Bugs

**Files:**
- Modify: `frontend/src/utils/api/orchestration.ts`
- Modify: `frontend/src/pages/execution/WorkflowRunMatrixPage.tsx`
- Modify: `frontend/src/components/pipeline/PipelineStepTree.tsx`
- Modify: `frontend/src/components/log/XTerminal.tsx`
- Test: `frontend/src/components/pipeline/PipelineStepTree.test.tsx`
- Test: `frontend/src/components/log/XTerminal.test.tsx`

- [ ] **Step 1: Add failing test for stable phase order**

Add a test where steps arrive as `execute`, `post_process`, `prepare`, and assert headings render as `prepare`, `execute`, `post_process`.

Run:

```powershell
npm test -- PipelineStepTree --run
```

Expected before implementation: FAIL because order follows input insertion.

- [ ] **Step 2: Implement fixed phase ordering**

In `PipelineStepTree.tsx`, replace `Array.from(map.entries())` ordering with:

```ts
const PHASE_ORDER = ['prepare', 'execute', 'post_process'];
const known = PHASE_ORDER.filter((phase) => map.has(phase));
const unknown = Array.from(map.keys()).filter((phase) => !PHASE_ORDER.includes(phase)).sort();
return [...known, ...unknown].map((name) => ({
  name,
  steps: [...(map.get(name) ?? [])].sort((a, b) => a.step_order - b.step_order),
}));
```

- [ ] **Step 3: Add failing artifact helper expectation**

In a matrix page test, mock one artifact and assert the download link uses `api.execution.artifactDownloadUrl(runId, jobId, artifactId)`.

Expected before implementation: FAIL because the page builds `/api/v1/runs/${jobId}/artifacts/...` inline.

- [ ] **Step 4: Fix artifact helper usage**

Change `artifactDownloadUrl` signature to:

```ts
artifactDownloadUrl: (runId: number, jobId: number, artifactId: number) =>
  `/api/v1/runs/${jobId}/artifacts/${artifactId}/download`,
```

Then update call sites to pass `(runId, jobId, artifactId)`. Keep backend route unchanged because it currently treats `run_id` as job id in `backend/api/routes/runs.py`; add a short frontend comment naming the legacy route parameter mismatch.

- [ ] **Step 5: Add failing terminal sanitization test**

In `XTerminal.test.tsx`, write a log line containing OSC sequence `\x1b]0;owned\x07` and assert the terminal mock receives no OSC payload.

- [ ] **Step 6: Sanitize terminal input before formatting**

Add:

```ts
function sanitizeTerminalInput(text: string): string {
  return text
    .replace(/\x1b\][0-9;?]*;[^\x07]*(\x07|\x1b\\)/g, '')
    .replace(/[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]/g, '');
}
```

Call it at the start of `formatLogLine`.

- [ ] **Step 7: Verify**

Run:

```powershell
npm test -- PipelineStepTree XTerminal --run
npm run type-check
```

Expected: targeted tests pass; type-check passes. If Vitest is blocked by local `esbuild spawn EPERM`, record that and rely on `npm run type-check`.

---

### Task 2: Add `enabled` Support End-to-End

**Files:**
- Modify: `backend/schemas/pipeline_schema.json`
- Modify: `backend/agent/pipeline_engine.py`
- Test: `backend/core/test_pipeline_validator.py`
- Test: `backend/agent/tests/test_pipeline_engine_script_action.py`
- Modify: `frontend/src/utils/api/types.ts`

- [ ] **Step 1: Add backend schema test**

Add a validator test with:

```python
pipeline = {
    "stages": {
        "prepare": [
            {
                "step_id": "skip_me",
                "action": "builtin:check_device",
                "timeout_seconds": 5,
                "retry": 0,
                "enabled": False,
            }
        ]
    }
}
assert validate_pipeline_def(pipeline)[0] is True
```

Run:

```powershell
$env:ALLOW_SQLITE_TESTS='1'; python -m pytest backend/core/test_pipeline_validator.py -q
```

Expected before implementation: FAIL because `additionalProperties: false` rejects `enabled`.

- [ ] **Step 2: Update JSON schema**

Add to `step.properties`:

```json
"enabled": { "type": "boolean", "default": true }
```

- [ ] **Step 3: Add engine skip test**

Add a pipeline engine test with one disabled step followed by an enabled step. Assert disabled action is not invoked and the enabled action is invoked.

- [ ] **Step 4: Implement skip in `PipelineEngine`**

At the start of step execution, before resolving action:

```python
if step.get("enabled") is False:
    return StepResult(success=True, skipped=True, skip_reason="step disabled")
```

If trace emission happens after `_execute_step_stages`, it should already record `SKIPPED` through existing `StepResult.skipped` handling.

- [ ] **Step 5: Add frontend type**

Update:

```ts
export interface PipelineStep {
  step_id: string;
  action: string;
  version?: string;
  params?: Record<string, any>;
  timeout_seconds: number;
  retry?: number;
  enabled?: boolean;
}
```

- [ ] **Step 6: Verify**

Run:

```powershell
$env:ALLOW_SQLITE_TESTS='1'; python -m pytest backend/core/test_pipeline_validator.py backend/agent/tests/test_pipeline_engine_script_action.py -q
npm run type-check
```

Expected: all pass.

---

### Task 3: Add Dispatch Preview and Parameter Overrides

**Files:**
- Modify: `backend/services/dispatcher.py`
- Modify: `backend/api/routes/orchestration.py`
- Test: `backend/tests/services/test_dispatcher_setup_pipeline.py`
- Modify: `frontend/src/utils/api/types.ts`
- Modify: `frontend/src/utils/api/orchestration.ts`

- [ ] **Step 1: Add service tests for preview**

In `test_dispatcher_setup_pipeline.py`, add tests asserting:

- preview uses the same setup/task/teardown composition as dispatch.
- disabled steps appear in preview with `enabled: false`.
- overrides update `params`, `timeout_seconds`, `retry`, and `enabled` before validation.

Use override shape:

```python
overrides = [
    {
        "template_name": "default",
        "stage": "execute",
        "step_id": "run_monkey",
        "params": {"duration": 600},
        "timeout_seconds": 700,
        "retry": 1,
        "enabled": True,
    }
]
```

- [ ] **Step 2: Create shared preview helpers**

In `dispatcher.py`, add:

```python
def _apply_step_overrides(pipeline: dict, template_name: str, overrides: list[dict] | None) -> dict:
    ...

async def preview_workflow_dispatch(
    workflow_def_id: int,
    device_ids: list[int],
    failure_threshold: float,
    db: AsyncSession,
    step_overrides: list[dict] | None = None,
) -> dict:
    ...
```

Preview output must include `workflow_definition_id`, `device_count`, `job_count`, `templates`, and per-template `resolved_pipeline`.

- [ ] **Step 3: Reuse preview resolution in dispatch**

Change `dispatch_workflow` signature:

```python
async def dispatch_workflow(..., step_overrides: list[dict] | None = None) -> WorkflowRun:
```

Apply overrides before assigning `JobInstance.pipeline_def`.

- [ ] **Step 4: Add API schemas and endpoint**

In `orchestration.py`, extend trigger:

```python
class PipelineStepOverride(BaseModel):
    template_name: str
    stage: Literal["prepare", "execute", "post_process"]
    step_id: str
    params: Optional[dict] = None
    timeout_seconds: Optional[int] = Field(default=None, ge=1)
    retry: Optional[int] = Field(default=None, ge=0, le=10)
    enabled: Optional[bool] = None
```

Add:

```python
@router.post("/workflows/{wf_id}/run/preview", response_model=ApiResponse[dict])
```

- [ ] **Step 5: Add frontend API types and method**

Add `PipelineStepOverride`, `WorkflowRunPreview`, and:

```ts
previewRun: (id: number, data: WorkflowRunCreate) =>
  unwrapApiResponse<WorkflowRunPreview>(apiClient.post(`/workflows/${id}/run/preview`, data)),
```

- [ ] **Step 6: Verify**

Run:

```powershell
$env:ALLOW_SQLITE_TESTS='1'; python -m pytest backend/tests/services/test_dispatcher_setup_pipeline.py -q
npm run type-check
```

Expected: all pass.

---

### Task 4: Refactor `StagesPipelineEditor` Interactions

**Files:**
- Modify: `frontend/src/components/pipeline/StagesPipelineEditor.tsx`
- Test: `frontend/src/components/pipeline/StagesPipelineEditor.test.tsx`

- [ ] **Step 1: Add failing tests**

Cover:

- Inline edit changes `step_id`, `timeout_seconds`, and `retry`.
- Duplicate creates a second step with a unique `step_id`.
- Disable toggles `enabled` to `false`.
- Up/down buttons reorder steps.
- Drag reorder calls `onChange` with reordered array.

Run:

```powershell
npm test -- StagesPipelineEditor --run
```

Expected before implementation: FAIL for the new interactions.

- [ ] **Step 2: Add pure helpers inside the component file**

Use helpers to keep UI event handlers small:

```ts
function normalizeStepEnabled(step: PipelineStep): PipelineStep {
  return { ...step, enabled: step.enabled !== false };
}

function duplicateStep(step: PipelineStep, existing: PipelineStep[]): PipelineStep {
  const base = `${step.step_id}_copy`;
  let candidate = base;
  let n = 2;
  while (existing.some((s) => s.step_id === candidate)) {
    candidate = `${base}_${n}`;
    n += 1;
  }
  return { ...step, step_id: candidate, enabled: step.enabled !== false };
}
```

- [ ] **Step 3: Add inline controls**

Each step card gets stable-size inputs:

- text input for `step_id`
- number input for `timeout_seconds`
- number input for `retry`

Clamp values locally:

```ts
const timeout = Math.max(1, Number.parseInt(value, 10) || 1);
const retry = Math.min(10, Math.max(0, Number.parseInt(value, 10) || 0));
```

- [ ] **Step 4: Add action buttons**

Use lucide icons:

- `GripVertical` for drag handle
- `ArrowUp`, `ArrowDown`
- `Copy`
- `ToggleLeft` / `ToggleRight` or `EyeOff`
- `Settings` for drawer
- `Trash2`

Buttons must use `type="button"`, fixed dimensions, title/aria-label, and no scale hover transforms.

- [ ] **Step 5: Add `@dnd-kit` sorting**

Use existing dependencies:

```ts
DndContext
SortableContext
useSortable
arrayMove
verticalListSortingStrategy
```

Only reorder within the same stage.

- [ ] **Step 6: Verify**

Run:

```powershell
npm test -- StagesPipelineEditor --run
npm run type-check
```

Expected: tests/type-check pass or Vitest environment issue is recorded.

---

### Task 5: Add Multi-TaskTemplate Editing and Timeline

**Files:**
- Create: `frontend/src/components/pipeline/PipelineExecutionTimeline.tsx`
- Test: `frontend/src/components/pipeline/PipelineExecutionTimeline.test.tsx`
- Create: `frontend/src/pages/orchestration/workflowTemplateState.ts`
- Test: `frontend/src/pages/orchestration/workflowTemplateState.test.ts`
- Modify: `frontend/src/pages/orchestration/WorkflowDefinitionEditPage.tsx`

- [ ] **Step 1: Add helper tests**

Test helpers for:

- initializing templates from API data
- adding a unique template name
- duplicating selected template
- deleting selected template and selecting a neighbor
- producing sorted save payload
- detecting duplicate names

- [ ] **Step 2: Implement helper module**

Create helpers:

```ts
export function createTemplateName(existing: string[], base = 'task'): string;
export function sortTemplates<T extends { sort_order?: number; name: string }>(templates: T[]): T[];
export function hasDuplicateTemplateNames(templates: Array<{ name: string }>): boolean;
export function toTemplatePayload(templates: LocalTaskTemplate[]): Array<{ name: string; sort_order: number; pipeline_def: PipelineDef }>;
```

- [ ] **Step 3: Add timeline component test**

Input setup/task/teardown pipelines and assert rendered groups appear in this order:

```text
Setup Prepare
Task Prepare
Task Execute
Task Post Process
Teardown Post Process
```

- [ ] **Step 4: Implement timeline component**

Render a dense horizontal/stacked flow with counts and step IDs. Use badges and dividers; no nested cards.

- [ ] **Step 5: Refactor page state**

Replace `localPipeline` with:

```ts
const [taskTemplates, setTaskTemplates] = useState<LocalTaskTemplate[] | null>(null);
const [selectedTemplateKey, setSelectedTemplateKey] = useState<string | null>(null);
```

Load all `wf.task_templates`, not only index 0.

- [ ] **Step 6: Save complete template list**

Change save payload from hard-coded `default` to:

```ts
task_templates: toTemplatePayload(taskTemplates)
```

Block save if names are blank or duplicated.

- [ ] **Step 7: Verify**

Run:

```powershell
npm test -- workflowTemplateState PipelineExecutionTimeline --run
npm run type-check
```

Expected: pass.

---

### Task 6: Add Dispatch Preview Dialog

**Files:**
- Create: `frontend/src/pages/orchestration/DispatchPreviewDialog.tsx`
- Test: `frontend/src/pages/orchestration/DispatchPreviewDialog.test.tsx`
- Modify: `frontend/src/pages/orchestration/WorkflowDefinitionListPage.tsx`
- Modify: `frontend/src/utils/api/types.ts`
- Modify: `frontend/src/utils/api/orchestration.ts`

- [ ] **Step 1: Add dialog tests**

Mock `api.orchestration.previewRun` and `api.orchestration.run`. Assert:

- opening the dialog calls preview with selected devices and overrides.
- preview lists templates, stages, disabled steps, and job count.
- editing one override updates preview request.
- confirm dispatch calls `run` with the same payload used for preview.

- [ ] **Step 2: Implement dialog**

Use React Query mutation for preview and run. Keep local override state:

```ts
const [overrides, setOverrides] = useState<PipelineStepOverride[]>([]);
```

Render preview groups by template and stage.

- [ ] **Step 3: Wire list page launch**

Replace direct run button behavior with opening `DispatchPreviewDialog`.

- [ ] **Step 4: Verify**

Run:

```powershell
npm test -- DispatchPreviewDialog --run
npm run type-check
```

Expected: pass.

---

### Task 7: Improve Matrix and Logs Usability

**Files:**
- Modify: `frontend/src/pages/execution/WorkflowRunMatrixPage.tsx`
- Test: `frontend/src/pages/execution/WorkflowRunMatrixPage.test.tsx`
- Modify: `frontend/src/pages/logs/LogsPage.tsx`

- [ ] **Step 1: Add matrix tests**

Assert:

- device search filters visible jobs by serial/id/status reason.
- drawer uses responsive wide class, not fixed `w-96`.
- artifact download links come from the API helper.

- [ ] **Step 2: Implement matrix search and wider drawer**

Add:

```ts
const [query, setQuery] = useState('');
const filteredJobs = allJobs.filter((job) => matchesJobQuery(job, query));
```

Use `filteredJobs` for the matrix while summary counts still use `allJobs`.

Change drawer class to:

```tsx
className="fixed inset-y-0 right-0 z-50 w-full max-w-5xl bg-white shadow-2xl border-l flex flex-col"
```

- [ ] **Step 3: Add log line numbers and highlight**

In `LogsPage`, render each visible row with a line-number gutter based on absolute index. Highlight keywords by splitting text into React spans, not HTML:

```ts
function highlightLogText(text: string): React.ReactNode[] {
  const parts = text.split(/(\bFATAL\b|\bCRASH\b|\bANR\b)/gi);
  return parts.map((part, index) =>
    /^(FATAL|CRASH|ANR)$/i.test(part)
      ? <mark key={index} className="rounded bg-amber-100 px-0.5 text-amber-900">{part}</mark>
      : <span key={index}>{part}</span>
  );
}
```

- [ ] **Step 4: Bulk actions boundary**

Do not add fake retry controls. If no reliable backend endpoint exists in this pass, render no retry button. Add bulk terminate only after a backend abort endpoint is implemented and verified; otherwise leave a code comment out of the UI and document the gap in final status.

- [ ] **Step 5: Verify**

Run:

```powershell
npm test -- WorkflowRunMatrixPage --run
npm run type-check
```

Expected: pass.

---

### Task 8: Final Regression

**Files:** all touched files.

- [ ] **Step 1: Backend verification**

Run:

```powershell
$env:ALLOW_SQLITE_TESTS='1'; python -m pytest backend/core/test_pipeline_validator.py backend/tests/services/test_dispatcher_setup_pipeline.py backend/agent/tests/test_pipeline_engine_script_action.py -q
```

Expected: all pass.

- [ ] **Step 2: Frontend verification**

Run:

```powershell
npm run type-check
```

Expected: `tsc --noEmit` succeeds.

- [ ] **Step 3: Targeted frontend tests**

Run:

```powershell
npm test -- StagesPipelineEditor PipelineStepTree XTerminal workflowTemplateState PipelineExecutionTimeline DispatchPreviewDialog WorkflowRunMatrixPage --run
```

Expected: pass. If local Vitest fails with `esbuild spawn EPERM`, record the environment blocker and list which tests were not executed.

- [ ] **Step 4: Manual smoke**

Start the frontend/backend dev environment already used by the project, then verify:

- Workflow edit page loads existing workflow with all task templates.
- Add, copy, disable, reorder, save, reload.
- Dispatch preview shows setup/task/teardown resolved order.
- Run starts with same payload as preview.
- Matrix search filters jobs.
- Job drawer logs and artifacts are readable.

- [ ] **Step 5: Check git status**

Run:

```powershell
git -c safe.directory=F:/stability-test-platform status --short
```

Expected: only planned files changed plus existing pre-work changes.

