from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from typing import Set, Dict, Any, Optional
import asyncio
import json
import logging
import os

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

    # 验证 token（开发模式下允许默认 token）
    if token != _WS_TOKEN:
        logger.warning(f"WebSocket connection rejected: invalid token")
        await websocket.close(code=4001, reason="Invalid token")
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


@router.websocket("/ws/logs/{run_id}")
async def websocket_logs(websocket: WebSocket, run_id: int, token: Optional[str] = Query(None)):
    """WebSocket logs endpoint with token authentication."""
    if not await _validate_ws_token(websocket, token):
        return
    path = f"/ws/logs/{run_id}"
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
