"""Legacy WebSocket module — kept for backward compatibility.

All real-time communication now uses python-socketio (see backend/realtime/).
This module re-exports broadcast helpers so existing imports continue to work.
The legacy WS endpoints are retained as deprecated stubs that accept
connections but do not relay data.
"""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from typing import Set, Dict, Any, Optional
import asyncio
import json
import logging
import os

logger = logging.getLogger(__name__)

router = APIRouter(tags=["websocket"])

# ---------------------------------------------------------------------------
# Re-exports from the new SocketIO module (backward-compat for callers that
# still import from this module, e.g. tests).
# ---------------------------------------------------------------------------

from backend.realtime.socketio_server import (  # noqa: F401, E402
    broadcast_device_update,
    broadcast_job_log,
    broadcast_log_message,
    broadcast_run_job_update,
    broadcast_run_workflow_status,
    broadcast_run_update,
    broadcast_task_update,
    broadcast_report_ready,
    schedule_emit as schedule_broadcast,
    capture_main_loop,
)


# ---------------------------------------------------------------------------
# Deprecated ConnectionManager stub
# ---------------------------------------------------------------------------

class ConnectionManager:
    """Deprecated — kept so tests that reference ``manager`` don't crash."""

    def __init__(self):
        self.active_connections: Dict[str, Set[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, path: str):
        await websocket.accept()
        if path not in self.active_connections:
            self.active_connections[path] = set()
        self.active_connections[path].add(websocket)

    def disconnect(self, websocket: WebSocket, path: str):
        if path in self.active_connections:
            self.active_connections[path].discard(websocket)
            if not self.active_connections[path]:
                del self.active_connections[path]

    async def broadcast(self, path: str, message: Dict[str, Any]):
        """No-op broadcast stub — real broadcasts go through SocketIO."""
        pass


manager = ConnectionManager()


# ---------------------------------------------------------------------------
# Deprecated WebSocket endpoints (kept as stubs for clients still connecting)
# These will be fully removed in Phase 4.
# ---------------------------------------------------------------------------

@router.websocket("/ws/dashboard")
async def websocket_dashboard(websocket: WebSocket, token: Optional[str] = Query(None)):
    """Deprecated — use SocketIO /dashboard namespace instead."""
    await websocket.accept()
    await websocket.send_json({"type": "DEPRECATED", "message": "Use SocketIO /dashboard namespace"})
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass


@router.websocket("/ws/logs/{run_id}")
async def websocket_logs(websocket: WebSocket, run_id: int, token: Optional[str] = Query(None)):
    """Deprecated — use SocketIO /dashboard namespace, room run:{run_id}."""
    await websocket.accept()
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass


@router.websocket("/ws/jobs/{job_id}/logs")
async def websocket_job_logs(websocket: WebSocket, job_id: int, token: Optional[str] = Query(None)):
    """Deprecated — use SocketIO /dashboard namespace, room job:{job_id}."""
    await websocket.accept()
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass


@router.websocket("/ws/workflow-runs/{run_id}")
async def websocket_workflow_run(websocket: WebSocket, run_id: int, token: Optional[str] = Query(None)):
    """Deprecated — use SocketIO /dashboard namespace, room workflow:{run_id}."""
    await websocket.accept()
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass


@router.websocket("/ws/agent/{host_id}")
async def websocket_agent(websocket: WebSocket, host_id: str):
    """Deprecated — use SocketIO /agent namespace instead."""
    await websocket.accept()
    await websocket.send_json({"type": "DEPRECATED", "message": "Use SocketIO /agent namespace"})
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
