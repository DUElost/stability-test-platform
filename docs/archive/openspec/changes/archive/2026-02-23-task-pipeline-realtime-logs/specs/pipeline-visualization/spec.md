## ADDED Requirements

### Requirement: Pipeline step tree component
The frontend SHALL display a collapsible tree of pipeline phases and steps in the left panel of the TaskDetails page, showing real-time status for each step.

#### Scenario: Step tree renders pipeline structure
- **WHEN** the user opens a TaskDetails page for a run with `pipeline_def`
- **THEN** the left panel SHALL display phases as collapsible groups, each containing their steps as list items, in the order defined in the pipeline definition

#### Scenario: Step status icons
- **WHEN** a step has status PENDING
- **THEN** the step SHALL display a gray circle icon
- **WHEN** a step has status RUNNING
- **THEN** the step SHALL display a blue spinner (animated) icon
- **WHEN** a step has status COMPLETED
- **THEN** the step SHALL display a green checkmark icon
- **WHEN** a step has status FAILED
- **THEN** the step SHALL display a red X icon
- **WHEN** a step has status SKIPPED
- **THEN** the step SHALL display a gray strikethrough icon

#### Scenario: Active step highlighting
- **WHEN** a step transitions to RUNNING
- **THEN** the step row SHALL be highlighted with a blue left border and the containing phase SHALL auto-expand if collapsed

#### Scenario: Step duration display
- **WHEN** a step has both `started_at` and `finished_at` timestamps
- **THEN** the step row SHALL display the elapsed duration (e.g., "2m 34s") next to the status icon
- **WHEN** a step is RUNNING
- **THEN** the duration SHALL display as a live counter updating every second

### Requirement: Real-time step status updates via WebSocket
The step tree SHALL update in real-time by consuming `STEP_UPDATE` WebSocket messages.

#### Scenario: Step transitions to RUNNING
- **WHEN** the frontend receives `{"type": "STEP_UPDATE", "step_id": 7, "status": "RUNNING"}`
- **THEN** the step with id=7 SHALL immediately update its icon to the blue spinner and start the duration counter

#### Scenario: Step transitions to FAILED
- **WHEN** the frontend receives `{"type": "STEP_UPDATE", "step_id": 7, "status": "FAILED", "error_message": "Device disconnected"}`
- **THEN** the step SHALL update its icon to red X, stop the duration counter, and display the error message on hover/tooltip

### Requirement: Step selection switches log panel
The user SHALL be able to click a step in the tree to switch the right panel's log viewer to that step's log stream.

#### Scenario: Click to switch step logs
- **WHEN** the user clicks on step "clean_env" in the step tree
- **THEN** the right panel SHALL switch to display logs filtered to `step_id` matching "clean_env", and the step row SHALL be visually selected (highlighted background)

#### Scenario: Auto-follow running step
- **WHEN** a new step transitions to RUNNING and the user has not manually selected a different step
- **THEN** the log panel SHALL automatically switch to the newly running step's log stream

### Requirement: Phase collapse/expand
Pipeline phases in the step tree SHALL be collapsible with smart default behavior.

#### Scenario: Default expansion state
- **WHEN** the step tree first renders
- **THEN** the phase containing the currently RUNNING step SHALL be expanded, and all other phases SHALL be collapsed

#### Scenario: Manual collapse/expand
- **WHEN** the user clicks a phase header
- **THEN** the phase SHALL toggle between collapsed and expanded states

### Requirement: Legacy TaskDetails fallback
The TaskDetails page SHALL detect whether a run has pipeline steps and render the appropriate UI.

#### Scenario: Run with pipeline
- **WHEN** the user opens TaskDetails for a run that has associated RunStep records
- **THEN** the page SHALL render the pipeline step tree (left) + xterm log viewer (right) layout

#### Scenario: Run without pipeline (legacy)
- **WHEN** the user opens TaskDetails for a run with no RunStep records
- **THEN** the page SHALL render the current layout (task info left + LogViewer right) unchanged
