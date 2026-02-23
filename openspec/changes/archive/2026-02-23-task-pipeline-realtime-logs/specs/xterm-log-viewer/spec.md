## ADDED Requirements

### Requirement: xterm.js terminal log viewer component
The frontend SHALL replace the current LogViewer with an xterm.js-based terminal component for rendering log output with high performance.

#### Scenario: Log rendering in terminal
- **WHEN** the user views a step's logs
- **THEN** the system SHALL render log lines in an xterm.js Canvas-based terminal supporting ANSI color codes, with scrollback buffer capped at 10,000 lines

#### Scenario: Dynamic import for bundle optimization
- **WHEN** the TaskDetails page is loaded
- **THEN** the xterm.js library and addons SHALL be loaded via `React.lazy()` dynamic import, NOT included in the main application bundle

### Requirement: Real-time log streaming into xterm
The xterm.js terminal SHALL receive and render log lines in real-time from the WebSocket connection.

#### Scenario: Live log streaming
- **WHEN** the frontend receives `{"type": "STEP_LOG", "step_id": 7, "msg": "Starting monkey..."}` and step 7 is currently selected
- **THEN** the message SHALL be written to the xterm.js terminal within 100ms of receipt, with ANSI color coding based on log level (ERROR=red, WARN=yellow, DEBUG=gray, INFO=default)

#### Scenario: Log for non-selected step
- **WHEN** the frontend receives a STEP_LOG message for a step that is NOT currently selected
- **THEN** the message SHALL be buffered in memory (per-step buffer, max 5000 lines) but NOT written to the active xterm instance

#### Scenario: Step switch loads buffered logs
- **WHEN** the user switches to a different step
- **THEN** the xterm instance SHALL be cleared and all buffered log lines for the newly selected step SHALL be written to the terminal

### Requirement: xterm search functionality
The terminal SHALL support text search with regex support via the xterm-addon-search plugin.

#### Scenario: Text search
- **WHEN** the user opens the search bar (Ctrl+F or toolbar button) and types a search query
- **THEN** the terminal SHALL highlight all matching occurrences and navigate between them with next/previous buttons

#### Scenario: Regex search
- **WHEN** the user enables regex mode and enters `FATAL|CRASH|ANR`
- **THEN** the terminal SHALL highlight all lines matching the regex pattern

### Requirement: Keyword auto-highlighting
The terminal SHALL automatically highlight critical keywords in log output using ANSI escape sequences injected at write time.

#### Scenario: FATAL/CRASH highlighting
- **WHEN** a log line contains the word "FATAL" or "CRASH"
- **THEN** the keyword SHALL be rendered in bold red (`\x1b[1;31m`)

#### Scenario: ANR highlighting
- **WHEN** a log line contains "ANR"
- **THEN** the keyword SHALL be rendered in bold orange/yellow (`\x1b[1;33m`)

### Requirement: Terminal auto-resize
The terminal SHALL automatically resize to fit its container using the xterm-addon-fit plugin.

#### Scenario: Window resize
- **WHEN** the browser window is resized or the panel layout changes
- **THEN** the xterm.js terminal SHALL recalculate its column and row count to fill the available space within 200ms

### Requirement: Log download
The user SHALL be able to download the current step's log content as a text file.

#### Scenario: Download step logs
- **WHEN** the user clicks the download button in the terminal toolbar
- **THEN** the browser SHALL download a file named `run_{run_id}_step_{step_name}.log` containing all buffered log lines for the current step in plain text (ANSI codes stripped)

### Requirement: Auto-scroll behavior
The terminal SHALL auto-scroll to the bottom as new log lines arrive, with a toggle to disable auto-scroll.

#### Scenario: Auto-scroll enabled (default)
- **WHEN** new log lines arrive and auto-scroll is enabled
- **THEN** the terminal SHALL automatically scroll to show the latest line

#### Scenario: Auto-scroll paused on manual scroll
- **WHEN** the user manually scrolls up in the terminal
- **THEN** auto-scroll SHALL be automatically disabled and a "Resume auto-scroll" button SHALL appear

#### Scenario: Resume auto-scroll
- **WHEN** the user clicks the "Resume auto-scroll" button
- **THEN** the terminal SHALL scroll to the bottom and re-enable auto-scroll

### Requirement: xterm instance pooling
The frontend SHALL manage a pool of xterm.js instances to prevent memory growth when switching between steps.

#### Scenario: Instance reuse
- **WHEN** the user has viewed 4 different steps and the pool limit is 3
- **THEN** the oldest (least recently used) xterm instance SHALL be disposed via `terminal.dispose()` and a new instance SHALL be created for the 4th step

#### Scenario: Instance cleanup on page unmount
- **WHEN** the user navigates away from the TaskDetails page
- **THEN** all xterm instances in the pool SHALL be disposed to free Canvas and WebGL resources
