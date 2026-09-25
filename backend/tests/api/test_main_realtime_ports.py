"""组装根注入 /dashboard 入站端口：socketio_server 不 import services，靠 backend.main 接线。"""

from __future__ import annotations


def test_main_wires_dashboard_ports():
    import backend.main  # noqa: F401 - 导入即完成组装
    import backend.realtime.socketio_server as sio_server
    import backend.services.realtime_ports as ports

    assert sio_server._authenticate_user is ports.authenticate_access_token
    assert sio_server._console_run_exists is ports.console_run_exists
