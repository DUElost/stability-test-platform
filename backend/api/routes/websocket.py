from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from typing import Set, Dict, Any, Optional
import asyncio
import json
import logging
import os
import time

logger = logging.getLogger(__name__)

router = APIRouter(tags=["websocket"])

# 用于验证 WebSocket 连接的 token（从环境变量读取或使用默认的开发 token）
_WS_TOKEN = os.getenv("WS_TOKEN", "dev-token-12345")


class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, Set[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, path: str):
        await websocket.accept()
        if path not in self.active_connections:
            self.active_connections[path] = set()
        self.active_connections[path].add(websocket)
        logger.info(f"WebSocket connected: {path}, total connections: {len(self.active_connections[path])}")

    def disconnect(self, websocket: WebSocket, path: str):
        if path in self.active_connections:
            self.active_connections[path].discard(websocket)
            if not self.active_connections[path]:
                del self.active_connections[path]
        logger.info(f"WebSocket disconnected: {path}")

    async def broadcast(self, path: str, message: Dict[str, Any]):
        if path not in self.active_connections:
            return
        disconnected = set()
        for connection in self.active_connections[path]:
            try:
                await connection.send_json(message)
            except Exception as e:
                logger.warning(f"Failed to send to WebSocket: {e}")
                disconnected.add(connection)
        # 清理断开的连接
        for conn in disconnected:
            self.disconnect(conn, path)


manager = ConnectionManager()


async def _validate_ws_token(websocket: WebSocket, token: Optional[str]) -> bool:
    """
    验证 WebSocket 连接 token。

    Returns:
        True if token is valid, False otherwise.
        如果没有配置 token（生产模式），则拒绝所有连接。
    """
    # 生产环境必须配置 WS_TOKEN
    if not os.getenv("WS_TOKEN") and os.getenv("ENV") == "production":
        logger.warning("WebSocket connection rejected: WS_TOKEN not configured in production")
        await websocket.close(code=4001, reason="Authentication required")
        return False

    # 验证 token：接受 WS_TOKEN 或有效的 JWT token
    if token:
        if token == _WS_TOKEN:
            return True

        # 尝试验证 JWT token
        try:
            from backend.core.security import decode_token
            payload = decode_token(token)
            if payload:
                # JWT token 验证成功
                return True
        except Exception:
            pass

        # JWT 验证失败，拒绝连接
        logger.warning(f"WebSocket connection rejected: invalid token")
        await websocket.close(code=4001, reason="Invalid token")
        return False

    # 没有 token 但在生产环境
    if os.getenv("ENV") == "production":
        logger.warning("WebSocket connection rejected: no token in production")
        await websocket.close(code=4001, reason="Authentication required")
        return False

    return True


async def _dashboard_socket_loop(websocket: WebSocket):
    path = "/ws/dashboard"
    await manager.connect(websocket, path)
    try:
        while True:
            data = await websocket.receive_text()
            # 处理客户端发送的消息
            try:
                message = json.loads(data)
                logger.debug(f"Received message on {path}: {message}")
            except json.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        manager.disconnect(websocket, path)
    except Exception as e:
        logger.error(f"WebSocket error on {path}: {e}")
        manager.disconnect(websocket, path)


@router.websocket("/ws/dashboard")
async def websocket_dashboard(websocket: WebSocket, token: Optional[str] = Query(None)):
    """WebSocket dashboard endpoint with token authentication."""
    if not await _validate_ws_token(websocket, token):
        return
    await _dashboard_socket_loop(websocket)


@router.websocket("/dashboard")
async def websocket_dashboard_legacy(websocket: WebSocket, token: Optional[str] = Query(None)):
    """Deprecated websocket endpoint - now requires token authentication."""
    logger.warning("Deprecated websocket endpoint '/dashboard' connected, use '/ws/dashboard' instead.")
    if not await _validate_ws_token(websocket, token):
        return
    await _dashboard_socket_loop(websocket)


@router.websocket("/ws/workflow-runs/{run_id}")
async def websocket_workflow_run(websocket: WebSocket, run_id: int, token: Optional[str] = Query(None)):
    """Frontend subscribes to real-time job/workflow status updates for a WorkflowRun."""
    if not await _validate_ws_token(websocket, token):
        return
    path = f"/ws/workflow-runs/{run_id}"
    await manager.connect(websocket, path)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket, path)
    except Exception as e:
        logger.error(f"WebSocket error on {path}: {e}")
        manager.disconnect(websocket, path)


@router.websocket("/ws/jobs/{job_id}/logs")
async def websocket_job_logs(websocket: WebSocket, job_id: int, token: Optional[str] = Query(None)):
    """Frontend subscribes to per-job log stream."""
    if not await _validate_ws_token(websocket, token):
        return
    path = f"/ws/jobs/{job_id}/logs"
    await manager.connect(websocket, path)
    # Send recent logs on connect (best-effort), to avoid missing fast jobs
    await _send_recent_job_logs(websocket, job_id)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket, path)
    except Exception as e:
        logger.error(f"WebSocket error on {path}: {e}")
        manager.disconnect(websocket, path)


@router.websocket("/ws/logs/{run_id}")
async def websocket_logs(websocket: WebSocket, run_id: int, token: Optional[str] = Query(None)):
    """WebSocket logs endpoint with token authentication."""
    if not await _validate_ws_token(websocket, token):
        return
    path = f"/ws/logs/{run_id}"
    await manager.connect(websocket, path)
    # Send recent logs on connect (best-effort), to avoid missing fast jobs
    await _send_recent_job_logs(websocket, run_id)
    try:
        while True:
            data = await websocket.receive_text()
            # 处理客户端发送的消息
            try:
                message = json.loads(data)
                logger.debug(f"Received message on {path}: {message}")
            except json.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        manager.disconnect(websocket, path)
    except Exception as e:
        logger.error(f"WebSocket error on {path}: {e}")
        manager.disconnect(websocket, path)


# ---------------------------------------------------------------------------
# Thread-safe broadcast bridge
# ---------------------------------------------------------------------------
# The main asyncio event loop is captured at startup so that background
# threads (dispatcher, recycler) can schedule broadcasts safely.

_main_loop: Optional[asyncio.AbstractEventLoop] = None


def capture_main_loop() -> None:
    """Store a reference to the main event loop.  Call once at app startup."""
    global _main_loop
    _main_loop = asyncio.get_event_loop()


def schedule_broadcast(path: str, message: Dict[str, Any]) -> None:
    """Thread-safe broadcast — callable from any thread (dispatcher, recycler)."""
    if _main_loop is None or _main_loop.is_closed():
        logger.warning("main_loop_not_available_for_broadcast")
        return
    asyncio.run_coroutine_threadsafe(manager.broadcast(path, message), _main_loop)


# ---------------------------------------------------------------------------
# Broadcast helper functions
# ---------------------------------------------------------------------------
# All broadcasts use the standard envelope: {type, payload, timestamp}
# See ADR-0009 for the event envelope specification.

from datetime import datetime, timezone


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def broadcast_device_update(device_data: Dict[str, Any]):
    await manager.broadcast("/ws/dashboard", {
        "type": "DEVICE_UPDATE",
        "payload": device_data,
        "timestamp": _now_iso(),
    })


async def broadcast_log_message(run_id: int, log_data: Dict[str, Any]):
    await manager.broadcast(f"/ws/logs/{run_id}", {
        "type": "LOG",
        "payload": log_data,
        "timestamp": _now_iso(),
    })


async def broadcast_job_log(job_id: int, log_data: Dict[str, Any]):
    await manager.broadcast(f"/ws/jobs/{job_id}/logs", {
        "type": "STEP_LOG",
        "payload": log_data,
        "timestamp": _now_iso(),
    })


async def broadcast_run_job_update(run_id: int, job_id: int, status: str) -> None:
    """Notify frontend subscribers that a specific job's status changed."""
    await manager.broadcast(f"/ws/workflow-runs/{run_id}", {
        "type": "JOB_STATUS",
        "payload": {
            "job_id": job_id,
            "status": status,
        },
        "timestamp": _now_iso(),
    })


async def broadcast_run_workflow_status(run_id: int, status: str) -> None:
    """Notify frontend subscribers that the overall WorkflowRun reached a terminal status."""
    await manager.broadcast(f"/ws/workflow-runs/{run_id}", {
        "type": "WORKFLOW_STATUS",
        "payload": {
            "status": status,
        },
        "timestamp": _now_iso(),
    })


async def broadcast_run_update(
    run_id: int,
    task_id: int,
    status: str,
    progress: int = 0,
    message: str = "",
) -> None:
    """Broadcast a RUN_UPDATE event to the dashboard channel."""
    await manager.broadcast("/ws/dashboard", {
        "type": "RUN_UPDATE",
        "payload": {
            "run_id": run_id,
            "task_id": task_id,
            "status": status,
            "progress": progress,
            "message": message,
        },
        "timestamp": _now_iso(),
    })


async def broadcast_task_update(task_id: int, status: Optional[str] = None) -> None:
    """Broadcast a TASK_UPDATE event to the dashboard channel."""
    await manager.broadcast("/ws/dashboard", {
        "type": "TASK_UPDATE",
        "payload": {
            "task_id": task_id,
            "status": status,
        },
        "timestamp": _now_iso(),
    })


async def broadcast_report_ready(run_id: int, task_id: int) -> None:
    """Broadcast a REPORT_READY event to the dashboard channel."""
    await manager.broadcast("/ws/dashboard", {
        "type": "REPORT_READY",
        "payload": {
            "run_id": run_id,
            "task_id": task_id,
        },
        "timestamp": _now_iso(),
    })


# ---------------------------------------------------------------------------
# Agent WebSocket Endpoint (WS /ws/agent/{host_id})
# ---------------------------------------------------------------------------
# Accepts persistent connections from agents for real-time log streaming
# and step status updates. Relays log messages to frontend subscribers.

_AGENT_SECRET = os.getenv("AGENT_SECRET", "")
_agent_connections: Dict[str, WebSocket] = {}  # host_id -> WebSocket


def get_agent_connections() -> Dict[str, WebSocket]:
    """Get the current agent WebSocket connections map."""
    return _agent_connections


@router.websocket("/ws/agent/{host_id}")
async def websocket_agent(websocket: WebSocket, host_id: str):
    """WebSocket endpoint for agent connections. Authenticates via AGENT_SECRET."""
    await websocket.accept()

    # Wait for auth message (first message must be auth)
    try:
        raw = await asyncio.wait_for(websocket.receive_text(), timeout=10.0)
        auth_msg = json.loads(raw)
    except asyncio.TimeoutError:
        logger.warning(f"Agent WS auth timeout for host_id={host_id}")
        try:
            await websocket.close(code=4001, reason="Auth timeout")
        except Exception:
            pass  # Already closed, ignore
        return
    except WebSocketDisconnect:
        logger.debug(f"Agent WS disconnected before auth for host_id={host_id}")
        return  # Already closed, nothing to do
    except json.JSONDecodeError as e:
        logger.warning(f"Agent WS auth parse error for host_id={host_id}: {e}")
        try:
            await websocket.close(code=4001, reason="Invalid JSON")
        except Exception:
            pass
        return
    except Exception as e:
        logger.warning(f"Agent WS auth error for host_id={host_id}: {e}")
        try:
            await websocket.close(code=4001, reason="Auth error")
        except Exception:
            pass
        return

    if auth_msg.get("type") != "auth":
        await websocket.close(code=4001, reason="First message must be auth")
        return

    # Validate agent_secret
    provided_secret = auth_msg.get("agent_secret", "")
    if _AGENT_SECRET:
        if provided_secret != _AGENT_SECRET:
            logger.warning(f"Agent WS auth failed for host_id={host_id}: invalid secret")
            await websocket.close(code=4001, reason="Invalid agent secret")
            return
    else:
        # No AGENT_SECRET configured — allow in dev, reject in production
        if os.getenv("ENV", "").lower() == "production":
            logger.warning(f"Agent WS rejected for host_id={host_id}: AGENT_SECRET not configured in production")
            await websocket.close(code=4001, reason="AGENT_SECRET not configured")
            return
        logger.warning(f"Agent WS auth skipped for host_id={host_id}: AGENT_SECRET not configured (dev mode)")

    # Auth successful
    _agent_connections[host_id] = websocket
    logger.info(f"Agent WS connected: host_id={host_id}, total agents: {len(_agent_connections)}")

    # Send auth acknowledgment
    await websocket.send_json({"type": "auth_ack", "status": "ok"})

    # Set up keepalive ping task
    last_pong = time.time()

    async def ping_loop():
        nonlocal last_pong
        while True:
            await asyncio.sleep(30)
            try:
                await websocket.send_json({"type": "ping"})
            except Exception:
                break

    ping_task = asyncio.create_task(ping_loop())

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            msg_type = msg.get("type", "")

            if msg_type == "pong":
                last_pong = time.time()

            elif msg_type == "log":
                # Relay log message to frontend subscribers
                run_id = msg.get("run_id")
                job_id = msg.get("job_id")
                log_data = {
                    "type": "STEP_LOG",
                    "payload": {
                        "step_id": msg.get("step_id"),
                        "seq": msg.get("seq"),
                        "level": msg.get("level", "INFO"),
                        "ts": msg.get("ts", ""),
                        "msg": msg.get("msg", ""),
                    },
                    "timestamp": _now_iso(),
                }
                if run_id:
                    await manager.broadcast(f"/ws/logs/{run_id}", log_data)
                if job_id:
                    await manager.broadcast(f"/ws/jobs/{job_id}/logs", log_data)

            elif msg_type == "step_update":
                # Update RunStep in DB and broadcast to frontend
                run_id = msg.get("run_id")
                step_id = msg.get("step_id")
                if run_id and step_id:
                    # DB update happens asynchronously via thread pool
                    try:
                        _handle_agent_step_update(msg, agent_host_id=host_id)
                    except Exception as e:
                        logger.warning(f"Failed to handle step_update: {e}")

                    # Broadcast to frontend immediately (standard envelope)
                    step_data = {
                        "type": "STEP_UPDATE",
                        "payload": {
                            "step_id": step_id,
                            "status": msg.get("status"),
                            "progress": msg.get("progress"),
                            "started_at": msg.get("started_at"),
                            "finished_at": msg.get("finished_at"),
                            "exit_code": msg.get("exit_code"),
                            "error_message": msg.get("error_message"),
                        },
                        "timestamp": _now_iso(),
                    }
                    await manager.broadcast(f"/ws/logs/{run_id}", step_data)

            elif msg_type == "heartbeat":
                # Process heartbeat from agent (reuse existing heartbeat logic)
                try:
                    _handle_agent_heartbeat(host_id, msg)
                except Exception as e:
                    logger.warning(f"Failed to handle agent heartbeat via WS: {e}")

    except WebSocketDisconnect:
        logger.info(f"Agent WS disconnected: host_id={host_id}")
    except Exception as e:
        logger.error(f"Agent WS error for host_id={host_id}: {e}")
    finally:
        ping_task.cancel()
        _agent_connections.pop(host_id, None)
        logger.info(f"Agent WS cleaned up: host_id={host_id}, remaining agents: {len(_agent_connections)}")


def _parse_iso_timestamp(value: str) -> "datetime":
    """Parse ISO 8601 timestamp, handling both '+00:00' and 'Z' suffixes.

    Python 3.8's datetime.fromisoformat() does not support the 'Z' suffix.
    """
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


def _handle_agent_step_update(msg: dict, agent_host_id: Optional[str] = None) -> None:
    """Upsert StepTrace from agent WS step_update message. Runs in the main thread."""
    from backend.core.database import SessionLocal
    from backend.models.job import JobInstance, StepTrace
    from datetime import datetime, timezone

    # Agent 发送的 run_id 字段对应新模型的 job_id
    job_id = msg.get("run_id")
    step_id = msg.get("step_id")
    if not job_id or not step_id:
        return

    status = (msg.get("status") or "").upper()

    # 将 Agent 上报的状态映射到 StepTrace event_type
    _STATUS_TO_EVENT = {
        "RUNNING": "STARTED",
        "STARTED": "STARTED",
        "COMPLETED": "COMPLETED",
        "FAILED": "FAILED",
        "CANCELED": "CANCELED",
        "CANCELLED": "CANCELED",
    }
    event_type = _STATUS_TO_EVENT.get(status, "status_update")

    db = SessionLocal()
    try:
        # Host 归属校验：job 必须属于当前连接的 Agent
        if agent_host_id:
            job = db.get(JobInstance, int(job_id))
            if not job:
                logger.warning(f"step_update: job {job_id} not found, host={agent_host_id}")
                return
            if job.host_id and str(job.host_id) != str(agent_host_id):
                logger.warning(
                    f"step_update host mismatch: job {job_id} assigned to host {job.host_id}, "
                    f"message from host {agent_host_id}"
                )
                return

        # 解析时间戳
        now = datetime.now(timezone.utc)
        original_ts = now
        ts_src = msg.get("started_at") or msg.get("finished_at")
        if ts_src:
            try:
                original_ts = _parse_iso_timestamp(ts_src)
            except Exception:
                pass

        # 幂等 Upsert：(job_id, step_id, event_type) 唯一
        existing = (
            db.query(StepTrace)
            .filter(
                StepTrace.job_id == int(job_id),
                StepTrace.step_id == str(step_id),
                StepTrace.event_type == event_type,
            )
            .first()
        )
        if existing:
            existing.status = status
            if msg.get("error_message") is not None:
                existing.error_message = msg["error_message"]
            existing.original_ts = original_ts
        else:
            db.add(
                StepTrace(
                    job_id=int(job_id),
                    step_id=str(step_id),
                    stage=msg.get("stage", "execute"),
                    status=status,
                    event_type=event_type,
                    error_message=msg.get("error_message"),
                    original_ts=original_ts,
                    created_at=datetime.utcnow(),
                )
            )

        db.commit()
        logger.debug(f"step_trace_upsert: job={job_id} step={step_id} event={event_type} status={status}")
    except Exception as e:
        logger.warning(f"StepTrace upsert failed: job={job_id} step={step_id} error={e}")
        db.rollback()
    finally:
        db.close()


def _handle_agent_heartbeat(host_id: str, msg: dict) -> None:
    """Push agent heartbeat data to dashboard subscribers for real-time UI.

    Does NOT write to DB — the HTTP heartbeat endpoint (/api/v1/heartbeat)
    is the single authority for host/device state persistence.
    """
    stats = msg.get("stats", {})
    devices = stats.get("devices", [])

    # Broadcast device metrics to dashboard for instant UI refresh
    if devices:
        try:
            for dev in devices:
                schedule_broadcast("/ws/dashboard", {
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
                })
        except Exception as e:
            logger.debug("ws_heartbeat_broadcast_failed host=%s: %s", host_id, e)


async def _send_recent_job_logs(websocket: WebSocket, job_id: int, limit: int = 200) -> None:
    """Replay recent logs for a job from Redis to avoid missing fast-completing runs."""
    try:
        from backend.main import redis_client
        if not redis_client:
            return

        # Read a recent window and filter by job_id (log stream is global)
        window = max(500, limit * 3)
        entries = await redis_client.xrevrange("stp:logs", max="+", min="-", count=window)
        if not entries:
            return

        wanted = []
        job_id_str = str(job_id)
        for _msg_id, fields in entries:
            if fields.get("job_id") == job_id_str:
                wanted.append(fields)
                if len(wanted) >= limit:
                    break

        # Send in chronological order
        for fields in reversed(wanted):
            log_data = {
                "type": "STEP_LOG",
                "payload": {
                    "step_id": fields.get("tag") or "",
                    "seq": None,
                    "level": fields.get("level", "INFO"),
                    "ts": fields.get("timestamp", ""),
                    "msg": fields.get("message", ""),
                },
                "timestamp": _now_iso(),
            }
            await websocket.send_json(log_data)
    except Exception as e:
        logger.warning(f"Replay recent logs failed for job {job_id}: {e}")
