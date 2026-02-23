## ADDED Requirements

### Requirement: Agent WebSocket connection to backend
The agent SHALL establish a persistent WebSocket connection to the backend at `WS /ws/agent/{host_id}` on startup, authenticated via `AGENT_SECRET`.

#### Scenario: Successful connection
- **WHEN** the agent starts and `AGENT_SECRET` is configured
- **THEN** the agent SHALL connect to `ws://{API_URL}/ws/agent/{host_id}`, send an auth message `{"type": "auth", "agent_secret": "..."}`, and receive an acknowledgment before sending any data

#### Scenario: Authentication failure
- **WHEN** the agent sends an invalid `agent_secret`
- **THEN** the backend SHALL close the WebSocket connection with code 4001 and the agent SHALL fall back to HTTP heartbeat mode

#### Scenario: Connection refused
- **WHEN** the backend is unreachable or the WebSocket handshake fails
- **THEN** the agent SHALL log a warning and operate in HTTP-only fallback mode, retrying WebSocket connection with exponential backoff (1s, 2s, 4s, ... up to 30s max)

### Requirement: Real-time log line transmission
The agent SHALL send each log line as an individual WebSocket message immediately upon generation, tagged with run_id, step_id, and sequence number.

#### Scenario: Log line sent in real-time
- **WHEN** a step action produces a log line
- **THEN** the agent SHALL send `{"type": "log", "run_id": 42, "step_id": 7, "seq": 1234, "level": "INFO", "ts": "2026-02-23T01:23:45.678Z", "msg": "Starting process..."}` within 100ms

#### Scenario: Sequence number tracking
- **WHEN** the agent sends log messages for a run
- **THEN** each message SHALL have a monotonically increasing `seq` number starting from 1, unique per run_id

### Requirement: WebSocket reconnection with message replay
The agent SHALL automatically reconnect on WebSocket disconnection and replay any buffered messages that were not acknowledged.

#### Scenario: Reconnection after disconnect
- **WHEN** the WebSocket connection drops during task execution
- **THEN** the agent SHALL buffer log messages in memory (up to 1000 lines), reconnect with exponential backoff, and replay buffered messages with their original sequence numbers after reconnection

#### Scenario: Buffer overflow during extended disconnect
- **WHEN** the WebSocket is disconnected and the buffer exceeds 1000 lines
- **THEN** the agent SHALL drop the oldest messages, log a warning with the count of dropped messages, and continue buffering new messages

### Requirement: HTTP heartbeat fallback for logs
The agent SHALL fall back to HTTP heartbeat-based log delivery when the WebSocket connection is unavailable.

#### Scenario: Fallback activation
- **WHEN** the WebSocket connection is not established or has been disconnected for more than 30 seconds
- **THEN** the agent SHALL batch log lines (up to 50 per request) and send them via the existing HTTP heartbeat POST to `/api/v1/agent/runs/{run_id}/heartbeat` with `log_lines` payload

#### Scenario: Fallback deactivation
- **WHEN** the WebSocket connection is re-established
- **THEN** the agent SHALL stop sending logs via HTTP heartbeat and resume WebSocket-only delivery

### Requirement: Backend agent WebSocket endpoint
The backend SHALL expose a WebSocket endpoint `WS /ws/agent/{host_id}` that accepts connections from agents, authenticates them, and relays log messages to frontend subscribers.

#### Scenario: Agent connection accepted
- **WHEN** an agent connects to `/ws/agent/{host_id}` with a valid auth message
- **THEN** the backend SHALL register the connection and begin accepting log, step_update, and heartbeat messages

#### Scenario: Log relay to frontend
- **WHEN** the backend receives a `log` message from an agent with `run_id: 42` and `step_id: 7`
- **THEN** the backend SHALL broadcast `{"type": "STEP_LOG", "step_id": 7, "seq": 1234, "level": "INFO", "ts": "...", "msg": "..."}` to all WebSocket clients subscribed to `/ws/logs/42`

#### Scenario: Step update relay
- **WHEN** the backend receives a `step_update` message from an agent
- **THEN** the backend SHALL update the `run_steps` record in the database AND broadcast `{"type": "STEP_UPDATE", "step_id": 7, "status": "RUNNING", "progress": 35}` to frontend subscribers

### Requirement: WebSocket keepalive
The agent and backend SHALL exchange ping/pong frames to keep the connection alive through proxies and firewalls.

#### Scenario: Ping/pong exchange
- **WHEN** the WebSocket connection is idle for 30 seconds
- **THEN** the agent SHALL send a ping frame, and the backend SHALL respond with a pong frame. If no pong is received within 10 seconds, the agent SHALL consider the connection dead and initiate reconnection

### Requirement: Agent websockets dependency
The agent installation SHALL include the `websockets` Python library as a new dependency.

#### Scenario: Install script includes websockets
- **WHEN** `install_agent.sh` is executed
- **THEN** the pip install step SHALL include `websockets>=12.0` in the requirements
