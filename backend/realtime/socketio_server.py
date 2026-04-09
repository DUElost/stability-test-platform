"""
SocketIO server — replaces the legacy ConnectionManager.

Namespaces:
  /agent      — Agent connections: receive logs, step status, heartbeat relay
  /dashboard  — Frontend connections: push device updates, job status, logs

Auth:
  /agent      — X-Agent-Secret header in connect handshake (auth dict)
  /dashboard  — JWT or WS_TOKEN in connect handshake (auth dict)
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import socketio

from backend.core.metrics import record_socketio_connection

logger = logging.getLogger(__name__)

_AGENT_SECRET = os.getenv("AGENT_SECRET", "")
_WS_TOKEN = os.getenv("WS_TOKEN", "dev-token-12345")

_sio: Optional[socketio.AsyncServer] = None


def get_sio() -> socketio.AsyncServer:
    """Return the singleton AsyncServer. Raises if not yet created."""
    if _sio is None:
        raise RuntimeError("SocketIO server not initialized — call create_sio_server() first")
    return _sio


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_sio_server() -> socketio.AsyncServer:
    """Create and configure the SocketIO AsyncServer singleton."""
    global _sio

    cors_origins = os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
    origins = [o.strip() for o in cors_origins.split(",") if o.strip()] or "*"

    sio = socketio.AsyncServer(
        async_mode="asgi",
        cors_allowed_origins=origins,
        logger=False,
        engineio_logger=False,
        ping_timeout=60,
        ping_interval=25,
        max_http_buffer_size=1_000_000,
    )

    _register_agent_namespace(sio)
    _register_dashboard_namespace(sio)
    _sio = sio
    return sio


# ---------------------------------------------------------------------------
# /agent namespace
# ---------------------------------------------------------------------------

class AgentNamespace(socketio.AsyncNamespace):
    """Handles Agent connections on /agent namespace."""

    async def on_connect(self, sid: str, environ: dict, auth: dict | None = None):
        auth = auth or {}
        provided_secret = auth.get("agent_secret", "")
        host_id = auth.get("host_id", "")

        if _AGENT_SECRET and provided_secret != _AGENT_SECRET:
            logger.warning("agent_sio_auth_failed sid=%s host_id=%s", sid, host_id)
            raise socketio.exceptions.ConnectionRefusedError("Invalid agent secret")

        if not _AGENT_SECRET and os.getenv("ENV", "").lower() == "production":
            logger.warning("agent_sio_rejected sid=%s: AGENT_SECRET not configured in production", sid)
            raise socketio.exceptions.ConnectionRefusedError("AGENT_SECRET not configured")

        if not host_id:
            logger.warning("agent_sio_no_host_id sid=%s", sid)
            raise socketio.exceptions.ConnectionRefusedError("host_id required")

        async with self.session(sid) as session:
            session["host_id"] = host_id

        self.enter_room(sid, f"agent:{host_id}")
        record_socketio_connection("/agent", True)
        logger.info("agent_sio_connected sid=%s host_id=%s", sid, host_id)

    async def on_disconnect(self, sid: str):
        async with self.session(sid) as session:
            host_id = session.get("host_id", "?")
        record_socketio_connection("/agent", False)
        logger.info("agent_sio_disconnected sid=%s host_id=%s", sid, host_id)

    async def on_step_log(self, sid: str, data: dict):
        """Agent emits step_log → broadcast to dashboard subscribers + persist to file."""
        job_id = data.get("job_id")
        if not job_id:
            return

        log_payload = {
            "type": "STEP_LOG",
            "payload": {
                "step_id": data.get("step_id", ""),
                "seq": data.get("seq"),
                "level": data.get("level", "INFO"),
                "ts": data.get("ts", ""),
                "msg": data.get("msg", ""),
            },
            "timestamp": _now_iso(),
        }

        sio = get_sio()
        await sio.emit("step_log", log_payload, namespace="/dashboard", room=f"job:{job_id}")
        await sio.emit("step_log", log_payload, namespace="/dashboard", room=f"run:{data.get('run_id', job_id)}")

        try:
            from backend.realtime.log_writer import append_log_line
            await append_log_line(
                job_id=int(job_id),
                line=data.get("msg", ""),
                level=data.get("level", "INFO"),
                ts=data.get("ts", ""),
                step_id=data.get("step_id", ""),
            )
        except Exception:
            logger.debug("log_writer_append_failed job_id=%s", job_id, exc_info=True)

    async def on_step_update(self, sid: str, data: dict):
        """Agent emits step_update → broadcast to dashboard subscribers."""
        job_id = data.get("job_id") or data.get("run_id")
        if not job_id:
            return

        step_payload = {
            "type": "STEP_UPDATE",
            "payload": {
                "step_id": data.get("step_id"),
                "status": data.get("status"),
                "progress": data.get("progress"),
                "started_at": data.get("started_at"),
                "finished_at": data.get("finished_at"),
                "exit_code": data.get("exit_code"),
                "error_message": data.get("error_message"),
            },
            "timestamp": _now_iso(),
        }

        sio = get_sio()
        await sio.emit("step_update", step_payload, namespace="/dashboard", room=f"job:{job_id}")
        await sio.emit("step_update", step_payload, namespace="/dashboard", room=f"run:{data.get('run_id', job_id)}")

    async def on_job_status(self, sid: str, data: dict):
        """Agent emits intermediate job status (INIT_RUNNING, etc.) → broadcast only, no DB write."""
        job_id = data.get("job_id") or data.get("run_id")
        if not job_id:
            return

        status = data.get("status", "")
        payload = {
            "type": "JOB_STATUS",
            "payload": {
                "job_id": int(job_id),
                "status": status,
                "reason": data.get("reason", ""),
            },
            "timestamp": _now_iso(),
        }

        sio = get_sio()
        await sio.emit("job_status", payload, namespace="/dashboard", room=f"job:{job_id}")
        run_id = data.get("workflow_run_id") or data.get("run_id", job_id)
        await sio.emit("job_status", payload, namespace="/dashboard", room=f"workflow:{run_id}")

    async def on_heartbeat(self, sid: str, data: dict):
        """Agent relays heartbeat for instant dashboard refresh (no DB write)."""
        async with self.session(sid) as session:
            host_id = session.get("host_id", "")

        stats = data.get("stats", {})
        devices = stats.get("devices", [])
        if not devices:
            return

        sio = get_sio()
        for dev in devices:
            await sio.emit("device_update", {
                "type": "DEVICE_UPDATE",
                "payload": {
                    "serial": dev.get("serial"),
                    "status": "ONLINE" if dev.get("adb_connected") else "OFFLINE",
                    "battery_level": dev.get("battery_level"),
                    "temperature": dev.get("temperature"),
                    "network_latency": dev.get("network_latency"),
                    "adb_state": dev.get("adb_state"),
                    "adb_connected": dev.get("adb_connected"),
                    "host_id": host_id,
                },
                "timestamp": _now_iso(),
            }, namespace="/dashboard")


# ---------------------------------------------------------------------------
# /dashboard namespace
# ---------------------------------------------------------------------------

class DashboardNamespace(socketio.AsyncNamespace):
    """Handles Frontend connections on /dashboard namespace."""

    async def on_connect(self, sid: str, environ: dict, auth: dict | None = None):
        auth = auth or {}
        token = auth.get("token", "")

        if os.getenv("ENV", "").lower() == "production" and not token:
            raise socketio.exceptions.ConnectionRefusedError("Authentication required")

        if token:
            if token == _WS_TOKEN:
                pass  # dev token accepted
            else:
                try:
                    from backend.core.security import decode_token
                    payload = decode_token(token)
                    if not payload:
                        raise socketio.exceptions.ConnectionRefusedError("Invalid token")
                except Exception:
                    raise socketio.exceptions.ConnectionRefusedError("Invalid token")

        record_socketio_connection("/dashboard", True)
        logger.info("dashboard_sio_connected sid=%s", sid)

    async def on_disconnect(self, sid: str):
        record_socketio_connection("/dashboard", False)
        logger.info("dashboard_sio_disconnected sid=%s", sid)

    async def on_subscribe(self, sid: str, data: dict):
        """Client subscribes to specific rooms (job logs, workflow runs, etc.)."""
        room = data.get("room", "")
        if room:
            self.enter_room(sid, room)
            logger.debug("dashboard_subscribe sid=%s room=%s", sid, room)

    async def on_unsubscribe(self, sid: str, data: dict):
        """Client unsubscribes from a room."""
        room = data.get("room", "")
        if room:
            self.leave_room(sid, room)
            logger.debug("dashboard_unsubscribe sid=%s room=%s", sid, room)


def _register_agent_namespace(sio: socketio.AsyncServer) -> None:
    sio.register_namespace(AgentNamespace("/agent"))


def _register_dashboard_namespace(sio: socketio.AsyncServer) -> None:
    sio.register_namespace(DashboardNamespace("/dashboard"))


# ---------------------------------------------------------------------------
# Broadcast helpers (replacement for websocket.py broadcast_* functions)
# ---------------------------------------------------------------------------

async def broadcast_device_update(device_data: Dict[str, Any]) -> None:
    """Push a DEVICE_UPDATE to all dashboard subscribers."""
    sio = get_sio()
    await sio.emit("device_update", {
        "type": "DEVICE_UPDATE",
        "payload": device_data,
        "timestamp": _now_iso(),
    }, namespace="/dashboard")


async def broadcast_job_log(job_id: int, log_data: Dict[str, Any]) -> None:
    """Push a STEP_LOG to subscribers of a specific job."""
    sio = get_sio()
    payload = {
        "type": "STEP_LOG",
        "payload": log_data,
        "timestamp": _now_iso(),
    }
    await sio.emit("step_log", payload, namespace="/dashboard", room=f"job:{job_id}")


async def broadcast_log_message(run_id: int, log_data: Dict[str, Any]) -> None:
    """Push a LOG to subscribers of a run (legacy compat)."""
    sio = get_sio()
    await sio.emit("step_log", {
        "type": "LOG",
        "payload": log_data,
        "timestamp": _now_iso(),
    }, namespace="/dashboard", room=f"run:{run_id}")


async def broadcast_run_job_update(run_id: int, job_id: int, status: str) -> None:
    """Notify frontend that a specific job's status changed."""
    sio = get_sio()
    await sio.emit("job_status", {
        "type": "JOB_STATUS",
        "payload": {"job_id": job_id, "status": status},
        "timestamp": _now_iso(),
    }, namespace="/dashboard", room=f"workflow:{run_id}")


async def broadcast_run_workflow_status(run_id: int, status: str) -> None:
    """Notify frontend that the overall WorkflowRun reached a terminal status."""
    sio = get_sio()
    await sio.emit("workflow_status", {
        "type": "WORKFLOW_STATUS",
        "payload": {"status": status},
        "timestamp": _now_iso(),
    }, namespace="/dashboard", room=f"workflow:{run_id}")


async def broadcast_run_update(
    run_id: int, task_id: int, status: str,
    progress: int = 0, message: str = "",
) -> None:
    """Broadcast a RUN_UPDATE event to all dashboard subscribers."""
    sio = get_sio()
    await sio.emit("run_update", {
        "type": "RUN_UPDATE",
        "payload": {
            "run_id": run_id,
            "task_id": task_id,
            "status": status,
            "progress": progress,
            "message": message,
        },
        "timestamp": _now_iso(),
    }, namespace="/dashboard")


async def broadcast_task_update(task_id: int, status: Optional[str] = None) -> None:
    """Broadcast a TASK_UPDATE event to all dashboard subscribers."""
    sio = get_sio()
    await sio.emit("task_update", {
        "type": "TASK_UPDATE",
        "payload": {"task_id": task_id, "status": status},
        "timestamp": _now_iso(),
    }, namespace="/dashboard")


async def broadcast_report_ready(run_id: int, task_id: int) -> None:
    """Broadcast a REPORT_READY event to all dashboard subscribers."""
    sio = get_sio()
    await sio.emit("report_ready", {
        "type": "REPORT_READY",
        "payload": {"run_id": run_id, "task_id": task_id},
        "timestamp": _now_iso(),
    }, namespace="/dashboard")


# Thread-safe synchronous emit bridge (for recycler and other sync callers)
import asyncio

_main_loop: Optional[asyncio.AbstractEventLoop] = None


def capture_main_loop() -> None:
    """Store the main event loop reference for thread-safe emit."""
    global _main_loop
    _main_loop = asyncio.get_event_loop()


def schedule_emit(event: str, data: Dict[str, Any], namespace: str = "/dashboard", room: str | None = None) -> None:
    """Thread-safe emit — callable from any thread (recycler, etc.)."""
    if _main_loop is None or _main_loop.is_closed():
        logger.warning("main_loop_not_available_for_sio_emit")
        return
    try:
        sio = get_sio()
    except RuntimeError:
        logger.warning("sio_not_initialized_for_emit")
        return
    coro = sio.emit(event, data, namespace=namespace, room=room)
    asyncio.run_coroutine_threadsafe(coro, _main_loop)
