## MODIFIED Requirements

### Requirement: Pipeline editor component
The frontend SHALL provide a visual pipeline editor that allows users to construct pipeline definitions by adding, configuring, and reordering phases and steps.

#### Scenario: Add a new phase
- **WHEN** the user clicks "Add Phase" in the pipeline editor
- **THEN** a new phase card SHALL appear with a default name "New Phase", an editable name field, a parallel toggle, and an empty step list

#### Scenario: Add a step to a phase
- **WHEN** the user clicks "Add Step" within a phase
- **THEN** a new step form SHALL appear with fields for: name, action type (dropdown of built-in actions + registered tools), params form, timeout input, and on_failure dropdown (stop/continue/retry)

#### Scenario: Configure step parameters
- **WHEN** the user selects a builtin action type for a step
- **THEN** the editor SHALL render `DynamicToolForm` with the action's `param_schema` when the schema is non-empty, or a JSON textarea when the schema is empty or the action is a `shell:*` or `tool:*` type
- **AND** the form SHALL be pre-filled with default values from the schema merged with any existing step params
