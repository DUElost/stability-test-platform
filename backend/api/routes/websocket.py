from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from typing import Set, Dict, Any
import asyncio
import json
import logging

logger = logging.getLogger(__name__)

router = APIRouter(tags=["websocket"])


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
async def websocket_dashboard(websocket: WebSocket):
    await _dashboard_socket_loop(websocket)


@router.websocket("/dashboard")
async def websocket_dashboard_legacy(websocket: WebSocket):
    logger.warning("Deprecated websocket endpoint '/dashboard' connected, use '/ws/dashboard' instead.")
    await _dashboard_socket_loop(websocket)


@router.websocket("/ws/logs/{run_id}")
async def websocket_logs(websocket: WebSocket, run_id: int):
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
