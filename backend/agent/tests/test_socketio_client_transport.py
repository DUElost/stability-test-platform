"""#1121（R11-F13）：Agent SocketIO 强制 websocket-only——无 sticky 契约的前提。

polling 会话要求每个 HTTP 请求命中同一进程（会话亲和）；多实例 + 非 sticky LB
下该前提不成立。本测试用记录式假 Client 断言 connect 的 transports 参数。
"""
from __future__ import annotations

from types import SimpleNamespace

from backend.agent.socketio_client import AgentSocketIOClient


class _RecordingClient:
    last: "_RecordingClient | None" = None

    def __init__(self, **kwargs):
        self.init_kwargs = kwargs
        self.connect_kwargs: dict = {}
        self._handlers: dict = {}
        _RecordingClient.last = self

    def on(self, event, namespace=None):
        def deco(fn):
            self._handlers[(namespace, event)] = fn
            return fn

        return deco

    def connect(self, url, **kwargs):
        self.connect_kwargs = kwargs
        url_seen = url
        # 对齐真实库：触发 connect handler 置 _connected
        cb = self._handlers.get(("/agent", "connect"))
        if cb:
            cb()
        self.url_seen = url_seen


def test_agent_socketio_connect_forces_websocket_transport(monkeypatch):
    monkeypatch.setattr(
        "backend.agent.socketio_client._sio_lib",
        SimpleNamespace(Client=_RecordingClient),
    )
    monkeypatch.setattr(
        "backend.agent.socketio_client._HAS_SOCKETIO", True
    )

    client = AgentSocketIOClient(
        api_url="http://127.0.0.1:8000", host_id="host-1", agent_secret="s"
    )
    assert client.connect() is True

    rec = _RecordingClient.last
    assert rec is not None
    assert rec.connect_kwargs.get("transports") == ["websocket"], (
        "Agent 必须 websocket-only：polling 需会话亲和，与无 sticky 契约冲突（#1121）"
    )
    assert rec.connect_kwargs.get("namespaces") == ["/agent"]
