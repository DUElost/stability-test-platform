"""#4044：能力声明必须同源——心跳与 Socket.IO 握手不得各写一份名单。

判据：能力属于当前 Agent 实例，由**同一份构建内常量**同时供给两条通道。
若两处各写一份，就会出现「心跳说有能力、连接不支持载荷」或反之的伪证据。
"""

from __future__ import annotations

from types import SimpleNamespace

from backend.agent import heartbeat as hb
from backend.agent.contracts.capabilities import (
    AGENT_CAPABILITIES,
    SCAN_ROUND_V1,
    normalize_capabilities,
)
from backend.agent.socketio_client import AgentSocketIOClient


class _RecordingClient:
    last = None

    def __init__(self, *args, **kwargs):
        type(self).last = self
        self.connect_kwargs = {}
        self._handlers = {}

    def on(self, event, namespace=None):
        def deco(fn):
            self._handlers[(namespace, event)] = fn
            return fn
        return deco

    def connect(self, url, **kwargs):
        self.connect_kwargs = kwargs
        # 对齐真实库：connect 事件回调把客户端标记为已连接。
        handler = self._handlers.get(("/agent", "connect"))
        if handler:
            handler()


def _captured_post(monkeypatch, payload_holder):
    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"host_id": "101"}

    def fake_post(url, json=None, headers=None, timeout=None):
        payload_holder.append(json)
        return _Resp()

    monkeypatch.setattr(hb.requests, "post", fake_post)


def test_heartbeat_declares_the_build_capabilities(monkeypatch):
    seen: list[dict] = []
    _captured_post(monkeypatch, seen)

    hb.send_heartbeat("http://127.0.0.1:8000", 101, agent_instance_id="inst-1",
                      system_stats={}, mount_status={})

    assert seen[0]["capabilities"] == list(AGENT_CAPABILITIES)
    assert SCAN_ROUND_V1 in seen[0]["capabilities"]


def test_heartbeat_explicit_capabilities_are_normalised(monkeypatch):
    seen: list[dict] = []
    _captured_post(monkeypatch, seen)

    hb.send_heartbeat(
        "http://127.0.0.1:8000", 101,
        system_stats={}, mount_status={},
        capabilities=[SCAN_ROUND_V1, "", SCAN_ROUND_V1, "  ", 7, SCAN_ROUND_V1],
    )

    assert seen[0]["capabilities"] == [SCAN_ROUND_V1]


def test_socketio_handshake_uses_the_same_source(monkeypatch):
    monkeypatch.setattr("backend.agent.socketio_client._sio_lib",
                        SimpleNamespace(Client=_RecordingClient))
    monkeypatch.setattr("backend.agent.socketio_client._HAS_SOCKETIO", True)

    client = AgentSocketIOClient(
        api_url="http://127.0.0.1:8000", host_id="101", agent_secret="s",
        agent_instance_id="inst-1",
    )
    assert client.connect() is True
    auth = _RecordingClient.last.connect_kwargs["auth"]

    assert auth["capabilities"] == list(AGENT_CAPABILITIES)
    assert auth["agent_instance_id"] == "inst-1"


def test_normalize_rejects_non_iterable_and_scalar_input():
    assert normalize_capabilities(None) == ()
    assert normalize_capabilities(SCAN_ROUND_V1) == ()
    assert normalize_capabilities({"a": 1}) == ()
    assert normalize_capabilities(["b", "a", "a"]) == ("a", "b")


def test_normalize_trims_so_both_channels_agree():
    """心跳与握手共用归一化：带空白的同一能力必须落在同一个词上。"""
    assert normalize_capabilities([f" {SCAN_ROUND_V1} ", SCAN_ROUND_V1]) == (SCAN_ROUND_V1,)


def test_capability_source_is_a_build_level_constant():
    """Agent 侧只有一份能力名单：改名即两条通道同时改变，不会只改一边。"""
    from backend.agent.contracts.capabilities import FIRMWARE_CACHE_V1

    assert AGENT_CAPABILITIES == (SCAN_ROUND_V1, FIRMWARE_CACHE_V1)
