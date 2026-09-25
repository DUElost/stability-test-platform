"""realtime 测试：按生产组装注入 /dashboard 入站端口（backend/main.py 同一调用）。

测试直接构造 ``DashboardNamespace``，不经 ``backend.main``；不注入时端口 fail-closed
（token 一律拒绝、console 房间一律不放行），与生产行为不同。
"""

from __future__ import annotations

import pytest

import backend.realtime.socketio_server as sio_server
from backend.services.realtime_ports import wire_dashboard_ports


@pytest.fixture(autouse=True)
def _wired_dashboard_ports():
    previous = (sio_server._authenticate_user, sio_server._console_run_exists)
    wire_dashboard_ports()
    yield
    sio_server.configure_dashboard_ports(
        authenticate_user=previous[0], console_run_exists=previous[1],
    )
