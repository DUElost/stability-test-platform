"""/dashboard 入站能力的 services 侧实现，由组装根注入 socketio_server。

``backend/realtime/socketio_server.py`` 是 services 广泛使用的推送出口，不能反向
import services（否则 realtime ↔ services 成环）；它的 DashboardNamespace 所需的
「token 认证」与「console run 存在性」由这里提供，``backend/main.py`` 启动时调用
:func:`wire_dashboard_ports` 注入。
"""

from __future__ import annotations

from backend.core.database import SessionLocal
from backend.models.user import User
from backend.realtime.socketio_server import configure_dashboard_ports
from backend.services.auth_session import authenticate_token
from backend.services.run_console import RunConsole


def authenticate_access_token(token: str) -> User | None:
    """与 REST/metrics 同一校验面（#903）：PK 查库 + is_active + ver 纪元；
    ``expected_type="access"`` 防 refresh token 冒充 access。sync，调用方负责入线程池。"""
    with SessionLocal() as db:
        return authenticate_token(db, token, expected_type="access")


def console_run_exists(run_id: str) -> bool:
    """本进程 RunConsole 是否持有该 run（多实例下非本实例持有同样返回 False，#1114）。"""
    return RunConsole.instance().status(run_id) is not None


def wire_dashboard_ports() -> None:
    """把上面两项能力注入 socketio_server（幂等）。"""
    configure_dashboard_ports(
        authenticate_user=authenticate_access_token,
        console_run_exists=console_run_exists,
    )
