## Context

The `StagesPipelineEditor` lets users build pipeline definitions (prepare → execute → post_process stages). Each step has an action type and a params object. The full plumbing exists end-to-end:

- **Backend**: `builtin_actions.json` stores 17 action definitions with `param_schema` (type/label/required/placeholder/default per field). The `GET /api/v1/builtin-actions` API serves these with normalization against `ACTION_REGISTRY`.
- **Frontend**: `DynamicToolForm` renders forms from a `ParamSchema` (string/number/boolean/select fields). `StagesPipelineEditor` receives `builtinOptions` (including `param_schema`) and resolves the selected action via `getActionMeta()`.
- **Gap**: `StepEditorDrawer` renders params as a raw JSON textarea (lines 454-466) despite having access to `meta.selectedBuiltin.param_schema`. The form component and schema data never connect.

Additionally, 4 backend actions (`setup_device_commands`, `guard_process`, `run_shell_script`, `export_mobilelogs`) exist in `ACTION_REGISTRY` but have no `param_schema` in `builtin_actions.json` — the API auto-fills them with empty schemas.

## Goals / Non-Goals

**Goals:**
- Wire `DynamicToolForm` into `StepEditorDrawer` for builtin actions with non-empty `param_schema`
- Validate required params client-side before allowing step save
- Add `param_schema` for the 4 missing backend actions
- Add lightweight backend param validation (log warnings) in `pipeline_engine.py`

**Non-Goals:**
- Custom field types beyond existing string/number/boolean/select
- Server-side schema enforcement that blocks execution (warnings only)
- Redesigning `DynamicToolForm` layout or styling
- Editing `param_schema` from the frontend UI (admin-only, separate concern)

## Decisions

### D1: Conditional rendering — form vs textarea

**Choice**: When `meta.actionType === 'builtin'` and the resolved action has a non-empty `param_schema`, render `DynamicToolForm`. Otherwise (shell actions, tool actions, or builtin actions without schema), keep the JSON textarea.

**Rationale**: This approach requires zero migration. Existing pipelines with manually typed params continue to work. The textarea serves as an escape hatch for power users and for `shell:*` commands where params are freeform.

**Alternative considered**: Always show the form and hide the textarea — rejected because `shell:*` actions have no schema and tool actions use their own `param_schema` format from the tool catalog (different source, different lifecycle).

### D2: Two-way sync between form state and step.params

**Choice**: Maintain a local `Record<string, any>` state in the drawer, initialized from `step.params`. `DynamicToolForm.onChange(key, value)` updates this state. On save, merge the state into `step.params` and call `onChange(updatedStep)`. When action selection changes, reinitialize from the new action's defaults merged with any existing matching param keys.

**Rationale**: The existing `paramsText`/`handleParamsBlur` pattern serializes to JSON on blur. Replacing it with a structured state object avoids JSON parse/stringify roundtrips and naturally feeds `DynamicToolForm`'s `values` prop.

**Alternative considered**: Keep JSON string as source of truth and serialize/deserialize on every change — rejected due to unnecessary complexity and risk of parse errors mid-edit.

### D3: Required-field validation

**Choice**: Before saving a step, iterate `param_schema` entries where `required === true` and check that the corresponding key in params is non-empty (not `undefined`, not `''`). Show inline error text below the field and prevent save.

**Rationale**: `DynamicToolForm` already renders `*` indicators for required fields but doesn't enforce them. Adding validation at the drawer level is minimal code and catches errors before pipeline save.

### D4: Backend param validation — warn, don't block

**Choice**: In `pipeline_engine.py`, before executing a step with a `builtin:*` action, look up the action's `param_schema` from the loaded catalog. Log a warning for each required param that is missing or empty. Do not fail the step.

**Rationale**: Pipelines may be created outside the UI (API, templates). Hard-blocking would be a breaking change. Warnings surface misconfiguration without disrupting running tests.

### D5: Missing action schemas — add to `builtin_actions.json`

**Choice**: Define `param_schema` for the 4 missing actions by reading their implementation functions to identify what `ctx.params.get(...)` calls they make.

- `setup_device_commands`: reads device setup commands config — no user-facing params → empty schema
- `guard_process`: `package`, `activity`, `check_interval`, `max_restarts` — schema with 4 fields
- `run_shell_script`: `script_path`, `args`, `timeout` — schema with 3 fields
- `export_mobilelogs`: `output_dir`, `duration_hours` — schema with 2 fields

**Rationale**: Grounding schemas in actual `.get()` calls ensures accuracy. Actions with no meaningful user params get empty schemas (and the form shows "No configurable parameters").

## Risks / Trade-offs

**[Form ↔ JSON desync]** → Users who toggle between builtin actions may lose params typed for a previous action. Mitigation: on action change, preserve any matching keys and only clear keys not in the new schema.

**[Schema drift]** → If a backend action adds a new param but `builtin_actions.json` isn't updated, the form won't show the field. Mitigation: the JSON textarea fallback remains accessible via a toggle, and backend warnings catch missing required fields.

**[DynamicToolForm layout in drawer]** → The form uses `grid-cols-2` which may feel cramped in a narrow drawer. Mitigation: override with `grid-cols-1` via a wrapper className or pass a layout prop if needed during implementation.
