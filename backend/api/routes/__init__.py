from .auth import router as auth_router
from .heartbeat import router as heartbeat_router
from .hosts import router as hosts_router
from .tasks import router as tasks_router
from .websocket import router as websocket_router

__all__ = ["auth_router", "heartbeat_router", "hosts_router", "tasks_router", "websocket_router"]
