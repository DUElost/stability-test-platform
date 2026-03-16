## 1. Backend Action Schemas

- [x] 1.1 Add `param_schema` for `guard_process` in `builtin_actions.json` (grounded in actual ctx.params.get calls: `process_name`, `restart_command`, `max_restarts`, `resource_check_path`)
- [x] 1.2 Add `param_schema` for `run_shell_script` in `builtin_actions.json` (grounded: `script_path`, `command`, `timeout`, `capture_output`, `inject_serial`, `working_dir`)
- [x] 1.3 Add `param_schema` for `export_mobilelogs` in `builtin_actions.json` (grounded: `timestamps_from_step`, `mobilelog_path`, `local_dir`, `time_window_minutes`)
- [x] 1.4 Verify `setup_device_commands` has empty `param_schema` (no user-facing params) and document it

## 2. Frontend — DynamicToolForm Integration in StepEditorDrawer

- [x] 2.1 Add conditional rendering: show `DynamicToolForm` when action is `builtin:*` with non-empty `param_schema`, otherwise show JSON textarea
- [x] 2.2 Initialize form values from `step.params` merged with schema `default` values for missing keys
- [x] 2.3 Implement two-way state sync: `DynamicToolForm.onChange` updates local `Record<string, any>` state, save merges state into `step.params`
- [x] 2.4 Handle action change: preserve param values for keys in both old and new schemas, reset new-only keys to defaults
- [x] 2.5 Handle switch from builtin (with schema) to shell/tool/no-schema: serialize current params object as JSON into textarea

## 3. Frontend — Required-Field Validation

- [x] 3.1 Before save, validate all `param_schema` fields with `required: true` have non-empty values (not `undefined`, not `''`)
- [x] 3.2 Display inline error message below each invalid field
- [x] 3.3 Prevent step save when any required field is empty

## 4. Backend — Pipeline Engine Param Validation

- [x] 4.1 In `pipeline_engine.py`, load action's `param_schema` from the builtin actions catalog before step execution
- [x] 4.2 Log warning for each required param that is missing or empty (format: "Step {step_id}: missing required param '{key}' for action '{name}'")
- [x] 4.3 Ensure step execution proceeds regardless of validation warnings (warn, don't block)
