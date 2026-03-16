## Why

The `StagesPipelineEditor` currently renders action parameters as a raw JSON textarea, despite the full infrastructure already existing: `DynamicToolForm` component, `param_schema` definitions in `builtin_actions.json`, and API plumbing that delivers schema data to the editor. Users must manually type JSON to configure step params, which is error-prone and requires knowing the exact param keys and types. Connecting these existing pieces will make pipeline configuration intuitive and validated.

## What Changes

- Replace the raw JSON textarea in `StepEditorDrawer` with `DynamicToolForm` when a builtin action has a non-empty `param_schema`, falling back to the textarea for `shell:*` actions or actions without schema
- Add `param_schema` definitions for the 4 backend actions currently missing them (`setup_device_commands`, `guard_process`, `run_shell_script`, `export_mobilelogs`) in `builtin_actions.json`
- Add client-side required-field validation before step save, based on `param_schema.required` flags
- Add backend param validation in `pipeline_engine.py` against `param_schema` before step execution, logging warnings for missing required params

## Capabilities

### New Capabilities
- `param-form-integration`: Wire `DynamicToolForm` into `StepEditorDrawer` for schema-driven parameter editing with validation and JSON textarea fallback

### Modified Capabilities
- `pipeline-editor`: The step configuration experience changes from raw JSON to dynamic forms when schema is available

## Impact

- **Frontend**: `StagesPipelineEditor.tsx` (StepEditorDrawer section), imports `DynamicToolForm`
- **Backend data**: `builtin_actions.json` (add 4 missing action schemas)
- **Backend agent**: `pipeline_engine.py` (optional param validation before execution)
- **No breaking changes**: JSON textarea remains available as fallback; existing pipeline definitions are unaffected
