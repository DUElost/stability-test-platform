from backend.api.routes.auth import router as auth_router
from backend.api.routes.heartbeat import router as heartbeat_router
from backend.api.routes.hosts import router as hosts_router
from backend.api.routes.users import router as users_router

__all__ = ["auth_router", "heartbeat_router", "hosts_router", "users_router"]
