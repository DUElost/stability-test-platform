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

import asyncio
import logging
import os
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import socketio

from backend.core.agent_secret import AgentSecretNotConfiguredError, require_agent_secret
from backend.core.metrics import record_socketio_connection
from backend.core.security import ACCESS_COOKIE_NAME, extract_cookie_token

logger = logging.getLogger(__name__)

_WS_TOKEN = os.getenv("WS_TOKEN", "dev-token-12345")

_sio: Optional[socketio.AsyncServer] = None
_agent_ns: Optional["AgentNamespace"] = None


class AgentNotConnectedError(Exception):
    """Raised when an RPC targets a host that has no active agent connection."""

    def __init__(self, host_id: str):
        self.host_id = host_id
        super().__init__(f"agent for host '{host_id}' is not connected")


class AgentRpcError(Exception):
    """Raised when an Agent RPC fails (timeout, malformed ack, etc.)."""


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

    sio = socketio.AsyncServer(
        async_mode="asgi",
        cors_allowed_origins="*",
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

    def __init__(self, namespace: str):
        super().__init__(namespace)
        self._host_to_sid: dict[str, str] = {}
        self._lock = asyncio.Lock()

    def get_sid(self, host_id: str) -> Optional[str]:
        """Return the SocketIO sid for a connected agent, or None."""
        return self._host_to_sid.get(str(host_id))

    def connected_host_ids(self) -> list[str]:
        """Return host_ids with an active connection (testing helper)."""
        return list(self._host_to_sid.keys())

    async def on_connect(self, sid: str, environ: dict, auth: dict | None = None):
        auth = auth or {}
        provided_secret = auth.get("agent_secret", "")
        host_id = auth.get("host_id", "")

        try:
            expected = require_agent_secret()
        except AgentSecretNotConfiguredError:
            logger.warning("agent_sio_rejected sid=%s: AGENT_SECRET not configured", sid)
            raise socketio.exceptions.ConnectionRefusedError("AGENT_SECRET not configured")

        if not secrets.compare_digest(provided_secret or "", expected):
            logger.warning("agent_sio_auth_failed sid=%s host_id=%s", sid, host_id)
            raise socketio.exceptions.ConnectionRefusedError("Invalid agent secret")

        if not host_id:
            logger.warning("agent_sio_no_host_id sid=%s", sid)
            raise socketio.exceptions.ConnectionRefusedError("host_id required")

        async with self.session(sid) as session:
            session["host_id"] = host_id

        async with self._lock:
            self._host_to_sid[str(host_id)] = sid

        await self.enter_room(sid, f"agent:{host_id}")
        record_socketio_connection("/agent", True)
        logger.info("agent_sio_connected sid=%s host_id=%s", sid, host_id)

    async def on_disconnect(self, sid: str):
        async with self.session(sid) as session:
            host_id = session.get("host_id", "?")
        async with self._lock:
            tracked_sid = self._host_to_sid.get(str(host_id))
            if tracked_sid == sid:
                self._host_to_sid.pop(str(host_id), None)
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
        run_id = data.get("plan_run_id") or data.get("run_id", job_id)
        await sio.emit("job_status", payload, namespace="/dashboard", room=f"plan_run:{run_id}")

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
        if not token:
            token = extract_cookie_token(environ.get("HTTP_COOKIE"), ACCESS_COOKIE_NAME) or ""

        if os.getenv("ENV", "").lower() == "production" and not token:
            raise socketio.exceptions.ConnectionRefusedError("Authentication required")

        if token:
            if token == _WS_TOKEN:
                pass  # dev token accepted
            else:
                try:
                    from backend.core.security import decode_token
                    # ADR-0024 P0: expected_type="access" 防止 refresh token 通过
                    # cookie/auth 旁路冒充 access,绕过 logout 黑名单。
                    payload = decode_token(token, expected_type="access")
                    if not payload:
                        raise socketio.exceptions.ConnectionRefusedError("Invalid token")
                except socketio.exceptions.ConnectionRefusedError:
                    raise
                except Exception:
                    raise socketio.exceptions.ConnectionRefusedError("Invalid token")

        record_socketio_connection("/dashboard", True)
        logger.info("dashboard_sio_connected sid=%s", sid)

    async def on_disconnect(self, sid: str):
        record_socketio_connection("/dashboard", False)
        logger.info("dashboard_sio_disconnected sid=%s", sid)

    async def on_subscribe(self, sid: str, data: dict):
        """Client subscribes to specific rooms (job logs, plan runs, etc.)."""
        room = data.get("room", "")
        if room:
            await self.enter_room(sid, room)
            logger.debug("dashboard_subscribe sid=%s room=%s", sid, room)

    async def on_unsubscribe(self, sid: str, data: dict):
        """Client unsubscribes from a room."""
        room = data.get("room", "")
        if room:
            await self.leave_room(sid, room)
            logger.debug("dashboard_unsubscribe sid=%s room=%s", sid, room)


def _register_agent_namespace(sio: socketio.AsyncServer) -> None:
    global _agent_ns
    _agent_ns = AgentNamespace("/agent")
    sio.register_namespace(_agent_ns)


def _register_dashboard_namespace(sio: socketio.AsyncServer) -> None:
    sio.register_namespace(DashboardNamespace("/dashboard"))


def get_agent_namespace() -> "AgentNamespace":
    """Return the registered AgentNamespace instance.

    Raises ``RuntimeError`` if SocketIO has not been initialised yet.
    """
    if _agent_ns is None:
        raise RuntimeError(
            "AgentNamespace not registered — call create_sio_server() first"
        )
    return _agent_ns


async def call_agent_rpc(
    host_id: str,
    event: str,
    data: dict,
    *,
    timeout: float = 10.0,
) -> dict:
    """Invoke an RPC on a connected agent and await its ack response.

    Internally uses ``sio.call(event, data, to=sid, ...)`` which relies on
    the SocketIO ack mechanism.  The agent's handler must ``return`` the
    response value for it to be auto-forwarded as the ack payload.

    Raises:
        RuntimeError: if SocketIO has not been initialised.
        AgentNotConnectedError: if no agent is currently connected for ``host_id``.
        AgentRpcError: on RPC timeout or transport-level failure.
    """
    sio = get_sio()
    ns = get_agent_namespace()
    sid = ns.get_sid(host_id)
    if not sid:
        raise AgentNotConnectedError(str(host_id))

    try:
        ack = await sio.call(
            event,
            data,
            to=sid,
            namespace="/agent",
            timeout=timeout,
        )
    except asyncio.TimeoutError as exc:
        raise AgentRpcError(
            f"agent rpc '{event}' to host '{host_id}' timed out after {timeout}s"
        ) from exc
    except Exception as exc:
        raise AgentRpcError(
            f"agent rpc '{event}' to host '{host_id}' failed: {exc}"
        ) from exc

    if ack is None:
        raise AgentRpcError(
            f"agent rpc '{event}' to host '{host_id}' returned no ack payload"
        )
    if not isinstance(ack, dict):
        raise AgentRpcError(
            f"agent rpc '{event}' to host '{host_id}' returned non-dict ack: "
            f"{type(ack).__name__}"
        )
    return ack


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
    }, namespace="/dashboard", room=f"plan_run:{run_id}")


async def broadcast_plan_run_status(run_id: int, status: str) -> None:
    """Notify frontend that the overall PlanRun reached a terminal status."""
    sio = get_sio()
    await sio.emit("plan_run_status", {
        "type": "PLAN_RUN_STATUS",
        "payload": {"status": status},
        "timestamp": _now_iso(),
    }, namespace="/dashboard", room=f"plan_run:{run_id}")


async def broadcast_precheck_update(
    run_id: int,
    *,
    phase: str | None = None,
    dispatch_status: str | None = None,
) -> None:
    """ADR-0021 — notify PlanRun detail subscribers that dispatch gate state changed.

    Frontend treats this as an invalidation hint for ``run_context.precheck``
    and ``run_context.dispatch_state`` — payload carries only coarse progress
    markers, not the full precheck matrix.
    """
    sio = get_sio()
    await sio.emit(
        "precheck_update",
        {
            "type": "PRECHECK_UPDATE",
            "payload": {
                "phase": phase,
                "dispatch_status": dispatch_status,
            },
            "timestamp": _now_iso(),
        },
        namespace="/dashboard",
        room=f"plan_run:{run_id}",
    )


async def broadcast_watcher_signal(
    run_id: int,
    *,
    job_id: int,
    device_serial: Optional[str],
    category: str,
    inserted_count: int = 1,
) -> None:
    """ADR-0021 C5c — push watcher anomaly increment to plan_run subscribers.

    Frontend uses this purely as an *invalidation hint* — it triggers a
    refetch of `/plan-runs/{id}/watcher-summary` rather than mutating the
    cached payload directly.  This keeps the event payload tiny and avoids
    drift between the in-memory aggregate and the server-side window query.
    """
    sio = get_sio()
    await sio.emit(
        "watcher_signal",
        {
            "type": "WATCHER_SIGNAL",
            "payload": {
                "job_id": int(job_id),
                "device_serial": device_serial,
                "category": category,
                "inserted_count": int(inserted_count),
            },
            "timestamp": _now_iso(),
        },
        namespace="/dashboard",
        room=f"plan_run:{run_id}",
    )


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
