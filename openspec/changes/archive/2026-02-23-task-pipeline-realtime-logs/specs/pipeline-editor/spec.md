## ADDED Requirements

### Requirement: Pipeline editor component
The frontend SHALL provide a visual pipeline editor that allows users to construct pipeline definitions by adding, configuring, and reordering phases and steps.

#### Scenario: Add a new phase
- **WHEN** the user clicks "Add Phase" in the pipeline editor
- **THEN** a new phase card SHALL appear with a default name "New Phase", an editable name field, a parallel toggle, and an empty step list

#### Scenario: Add a step to a phase
- **WHEN** the user clicks "Add Step" within a phase
- **THEN** a new step form SHALL appear with fields for: name, action type (dropdown of built-in actions + registered tools), params form, timeout input, and on_failure dropdown (stop/continue/retry)

#### Scenario: Configure step parameters
- **WHEN** the user selects an action type for a step
- **THEN** the editor SHALL render a dynamic parameter form based on the action's JSON Schema (reusing the existing `DynamicToolForm` component), pre-filled with default values

### Requirement: Step drag-and-drop reordering
The pipeline editor SHALL support drag-and-drop reordering of steps within a phase and phases within the pipeline.

#### Scenario: Reorder steps within a phase
- **WHEN** the user drags a step from position 2 to position 1 within the same phase
- **THEN** the step order SHALL update visually and in the underlying pipeline definition JSON

#### Scenario: Reorder phases
- **WHEN** the user drags a phase from position 3 to position 1
- **THEN** the phase order SHALL update and the pipeline definition SHALL reflect the new execution order

### Requirement: Pipeline JSON preview
The editor SHALL provide a real-time JSON preview of the pipeline definition being constructed.

#### Scenario: Live preview update
- **WHEN** the user modifies any field in the pipeline editor (phase name, step action, params, etc.)
- **THEN** the JSON preview panel SHALL update immediately to reflect the current pipeline definition

#### Scenario: JSON validation feedback
- **WHEN** the pipeline definition is invalid (e.g., a step without an action)
- **THEN** the preview panel SHALL highlight the invalid section and display a validation error message

### Requirement: Pipeline template save and load
The user SHALL be able to save a pipeline definition as a reusable template and load existing templates into the editor.

#### Scenario: Save as template
- **WHEN** the user clicks "Save as Template" in the pipeline editor
- **THEN** the system SHALL create or update a `TaskTemplate` record with the current `pipeline_def` JSON, template name, and description

#### Scenario: Load from template
- **WHEN** the user clicks "Load Template" and selects an existing template
- **THEN** the pipeline editor SHALL populate with the template's pipeline definition, which the user can then modify before creating the task

#### Scenario: Built-in templates
- **WHEN** the user opens the template selector
- **THEN** the system SHALL display built-in templates for common test types (e.g., "Monkey Test Pipeline", "MTBF Pipeline") alongside user-created templates

### Requirement: Integration with task creation flow
The pipeline editor SHALL integrate into the existing task creation workflow as a new step.

#### Scenario: CreateTask with pipeline
- **WHEN** the user navigates to the task creation page
- **THEN** the creation flow SHALL include: Select Tool/Template -> Configure Pipeline -> Select Devices -> Review & Create

#### Scenario: Task creation payload
- **WHEN** the user submits the task creation form with a configured pipeline
- **THEN** the frontend SHALL send the `pipeline_def` JSON as part of the task creation payload, along with the standard task fields (name, type, target_device_id, etc.)

### Requirement: Action type browser
The pipeline editor SHALL provide a browsable list of available actions (built-in + registered tools) for step configuration.

#### Scenario: Browse built-in actions
- **WHEN** the user clicks the action selector for a step
- **THEN** a dropdown/modal SHALL display all built-in actions grouped by category (device, process, file, log), each showing name, description, and required parameters

#### Scenario: Browse registered tools
- **WHEN** the user switches to the "Tools" tab in the action browser
- **THEN** the system SHALL display all enabled Tools from the tool registry, grouped by category, with their description and parameter schema
