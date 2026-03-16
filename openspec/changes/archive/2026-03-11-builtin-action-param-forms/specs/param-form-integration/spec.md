## ADDED Requirements

### Requirement: Schema-driven parameter form rendering
The `StepEditorDrawer` SHALL render a `DynamicToolForm` for the step's parameters when the selected action is a `builtin:*` action with a non-empty `param_schema`. When no schema is available (empty schema, `shell:*` actions, or `tool:*` actions), the drawer SHALL render the existing JSON textarea.

#### Scenario: Builtin action with param_schema selected
- **WHEN** the user selects a builtin action that has a non-empty `param_schema` (e.g., `builtin:install_apk`)
- **THEN** the drawer SHALL render `DynamicToolForm` with the action's `param_schema`, displaying labeled input fields (text, number, checkbox, select) instead of the JSON textarea

#### Scenario: Builtin action without param_schema
- **WHEN** the user selects a builtin action that has an empty `param_schema` (e.g., `builtin:setup_device_commands`)
- **THEN** the drawer SHALL render the JSON textarea for freeform params entry

#### Scenario: Shell action selected
- **WHEN** the user selects a `shell:*` action
- **THEN** the drawer SHALL render the JSON textarea, since shell actions have no schema

#### Scenario: Tool action selected
- **WHEN** the user selects a `tool:*` action
- **THEN** the drawer SHALL render the JSON textarea for params entry

### Requirement: Form values initialized from step params
The `DynamicToolForm` SHALL be initialized from the step's existing `params` object, merged with the schema's `default` values for any keys not already present.

#### Scenario: Editing a step with existing params
- **WHEN** the user opens the drawer for a step that already has `params: { "apk_path": "/data/test.apk" }`
- **THEN** the form SHALL display `/data/test.apk` in the `apk_path` field, and fill all other fields with their schema-defined defaults

#### Scenario: New step with no params
- **WHEN** the user opens the drawer for a newly created step with `params: {}`
- **THEN** the form SHALL populate all fields with their schema-defined `default` values

### Requirement: Param state preserved on action change
When the user changes the selected builtin action, the drawer SHALL preserve param values for keys that exist in both the old and new action's schemas, and reset keys that are only in the new schema to their defaults.

#### Scenario: Switch between actions with overlapping params
- **WHEN** the user switches from `builtin:install_apk` (params: `apk_path`, `grant_permissions`) to `builtin:push_resources` (params: `local_path`, `remote_path`)
- **THEN** params with no matching key in the new schema SHALL be discarded, and new schema keys SHALL be initialized to their defaults

#### Scenario: Switch from builtin to shell
- **WHEN** the user switches from a builtin action to a `shell:*` action
- **THEN** the existing params object SHALL be serialized as JSON in the textarea

### Requirement: Required-field validation on save
The drawer SHALL validate that all `param_schema` fields marked `required: true` have non-empty values before allowing the step to be saved.

#### Scenario: Save with missing required field
- **WHEN** the user clicks save and a required param field is empty (undefined or empty string)
- **THEN** the drawer SHALL display an inline error message below the field and SHALL NOT save the step

#### Scenario: Save with all required fields filled
- **WHEN** the user clicks save and all required param fields have non-empty values
- **THEN** the drawer SHALL save the step with the form values as `step.params`

### Requirement: Complete param_schema for all registered actions
Every action in `ACTION_REGISTRY` SHALL have a corresponding entry in `builtin_actions.json` with an accurate `param_schema` reflecting the params the action implementation reads from `ctx.params`.

#### Scenario: guard_process action schema
- **WHEN** the API serves the `guard_process` action metadata
- **THEN** the `param_schema` SHALL include fields for `package` (string, required), `activity` (string), `check_interval` (number), and `max_restarts` (number)

#### Scenario: run_shell_script action schema
- **WHEN** the API serves the `run_shell_script` action metadata
- **THEN** the `param_schema` SHALL include fields for `script_path` (string, required), `args` (string), and `timeout` (number)

#### Scenario: export_mobilelogs action schema
- **WHEN** the API serves the `export_mobilelogs` action metadata
- **THEN** the `param_schema` SHALL include fields for `output_dir` (string) and `duration_hours` (number)

### Requirement: Backend param validation warnings
The pipeline engine SHALL validate step params against the action's `param_schema` before execution and log warnings for missing required params, without blocking execution.

#### Scenario: Step executed with missing required param
- **WHEN** a step with `builtin:install_apk` is executed and `apk_path` (required) is not in `ctx.params`
- **THEN** the engine SHALL log a warning: "Step {step_id}: missing required param 'apk_path' for action 'install_apk'"
- **AND** the step SHALL proceed with execution

#### Scenario: Step executed with all params present
- **WHEN** a step is executed with all required params present
- **THEN** no validation warnings SHALL be logged
