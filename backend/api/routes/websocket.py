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

# 辅助函数：广播设备更新
async def broadcast_device_update(device_data: Dict[str, Any]):
    await manager.broadcast("/ws/dashboard", {
        "type": "DEVICE_UPDATE",
        "payload": device_data
    })


# 辅助函数：广播日志消息
async def broadcast_log_message(run_id: int, log_data: Dict[str, Any]):
    await manager.broadcast(f"/ws/logs/{run_id}", {
        "type": "LOG",
        "payload": log_data
    })


async def broadcast_job_log(job_id: int, log_data: Dict[str, Any]):
    await manager.broadcast(f"/ws/jobs/{job_id}/logs", log_data)


async def broadcast_run_job_update(run_id: int, job_id: int, status: str) -> None:
    """Notify frontend subscribers that a specific job's status changed."""
    await manager.broadcast(f"/ws/workflow-runs/{run_id}", {
        "type": "job_status",
        "job_id": job_id,
        "status": status,
    })


async def broadcast_run_workflow_status(run_id: int, status: str) -> None:
    """Notify frontend subscribers that the overall WorkflowRun reached a terminal status."""
    await manager.broadcast(f"/ws/workflow-runs/{run_id}", {
        "type": "workflow_status",
        "status": status,
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
    })


async def broadcast_task_update(task_id: int, status: Optional[str] = None) -> None:
    """Broadcast a TASK_UPDATE event to the dashboard channel."""
    await manager.broadcast("/ws/dashboard", {
        "type": "TASK_UPDATE",
        "payload": {
            "task_id": task_id,
            "status": status,
        },
    })


async def broadcast_report_ready(run_id: int, task_id: int) -> None:
    """Broadcast a REPORT_READY event to the dashboard channel."""
    await manager.broadcast("/ws/dashboard", {
        "type": "REPORT_READY",
        "payload": {
            "run_id": run_id,
            "task_id": task_id,
        },
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
    except (asyncio.TimeoutError, json.JSONDecodeError, Exception) as e:
        logger.warning(f"Agent WS auth timeout/parse error for host_id={host_id}: {e}")
        await websocket.close(code=4001, reason="Auth timeout or invalid message")
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

                    # Broadcast to frontend immediately (wrapped in {type, payload} envelope)
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
    """Update RunStep record in DB from agent WS message. Runs in the main thread."""
    from backend.core.database import SessionLocal
    from backend.models.schemas import RunStep, RunStepStatus, TaskRun
    from datetime import datetime

    step_id = msg.get("step_id")
    if not step_id:
        return

    # Expected run_id from the message (for ownership validation)
    msg_run_id = msg.get("run_id")

    # Valid RunStep status transitions
    _STEP_TRANSITIONS = {
        RunStepStatus.PENDING: {RunStepStatus.RUNNING, RunStepStatus.SKIPPED, RunStepStatus.CANCELED},
        RunStepStatus.RUNNING: {RunStepStatus.COMPLETED, RunStepStatus.FAILED, RunStepStatus.CANCELED},
        RunStepStatus.COMPLETED: set(),
        RunStepStatus.FAILED: set(),
        RunStepStatus.SKIPPED: set(),
        RunStepStatus.CANCELED: set(),
    }

    db = SessionLocal()
    try:
        step = db.get(RunStep, step_id)
        if not step:
            return

        # Validate run_id ownership: step must belong to the run in the message
        if msg_run_id and step.run_id != msg_run_id:
            logger.warning(
                f"step_update ownership mismatch: step {step_id} belongs to run {step.run_id}, "
                f"but message claims run {msg_run_id} from host {agent_host_id}"
            )
            return

        # Validate host ownership: run must be assigned to the connecting agent's host
        if agent_host_id:
            run = db.get(TaskRun, step.run_id)
            if run and str(run.host_id) != str(agent_host_id):
                logger.warning(
                    f"step_update host mismatch: run {step.run_id} is assigned to host {run.host_id}, "
                    f"but update came from host {agent_host_id}"
                )
                return

        status_str = msg.get("status")
        if status_str:
            try:
                target_status = RunStepStatus(status_str)
                # Validate transition
                allowed = _STEP_TRANSITIONS.get(step.status, set())
                if target_status in allowed or step.status == target_status:
                    step.status = target_status
                else:
                    logger.warning(f"Invalid RunStep transition {step.status.value}->{status_str} for step {step_id}")
            except ValueError:
                pass
        if msg.get("started_at"):
            try:
                step.started_at = _parse_iso_timestamp(msg["started_at"])
            except (ValueError, TypeError):
                pass
        if msg.get("finished_at"):
            try:
                step.finished_at = _parse_iso_timestamp(msg["finished_at"])
            except (ValueError, TypeError):
                pass
        if msg.get("exit_code") is not None:
            step.exit_code = msg["exit_code"]
        if msg.get("error_message") is not None:
            step.error_message = msg["error_message"]

        db.commit()
    except Exception as e:
        logger.warning(f"DB update for step {step_id} failed: {e}")
        db.rollback()
    finally:
        db.close()


def _handle_agent_heartbeat(host_id: str, msg: dict) -> None:
    """Process heartbeat received via agent WebSocket."""
    from backend.core.database import SessionLocal
    from backend.models.enums import HostStatus
    from backend.models.host import Host
    from datetime import datetime, timezone

    db = SessionLocal()
    try:
        host = db.get(Host, host_id)
        if not host:
            return

        host.status = HostStatus.ONLINE.value
        host.last_heartbeat = datetime.now(timezone.utc)

        stats = msg.get("stats", {})
        if stats:
            host.extra = {**(host.extra or {}), **stats}

        db.commit()
    except Exception as e:
        logger.warning(f"Agent heartbeat DB update for host {host_id} failed: {e}")
        db.rollback()
    finally:
        db.close()


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
            }
            await websocket.send_json(log_data)
    except Exception as e:
        logger.warning(f"Replay recent logs failed for job {job_id}: {e}")
