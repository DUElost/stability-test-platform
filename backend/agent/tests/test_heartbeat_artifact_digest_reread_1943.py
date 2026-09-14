"""#1943：心跳的 agent_artifact_digest 必须逐拍重读。

ADR-0040 D3 的 no-op 稳态依赖 current digest 及时上报；而 write-digest 在
重启探活通过后才落盘——启动单读的值永远落后一轮（48 台 47 空的生产实证）。
修复：HeartbeatThread 接受 callable 提供方，每次发送前解析（72B 文件读取，
与 D2「不做每次心跳全树重算」不冲突）；字符串形态保留向后兼容。
"""

from backend.agent.heartbeat_thread import HeartbeatThread


class FakeResponse:
    def raise_for_status(self):
        return None

    def json(self):
        return {"ok": True, "script_catalog_outdated": False}


def _make_thread(monkeypatch, provider, sent_payloads):
    def fake_send_heartbeat(*args, **kwargs):
        sent_payloads.append(kwargs)
        return {"ok": True}

    monkeypatch.setattr("backend.agent.heartbeat_thread.send_heartbeat", fake_send_heartbeat)
    monkeypatch.setattr(
        "backend.agent.heartbeat_thread.device_discovery.discover_devices", lambda adb: [],
    )
    return HeartbeatThread(
        api_url="http://server",
        host_id="host-1",
        adb_path="adb",
        mount_points=[],
        host_info={},
        poll_interval=60,
        agent_artifact_digest=provider,
    )


def test_callable_provider_reread_each_tick(monkeypatch):
    """write-digest 晚于重启落盘：同一进程内文件变化必须在下一拍可见。"""
    values = iter(["", "sha256:" + "a" * 64, "sha256:" + "b" * 64])
    sent = []
    thread = _make_thread(monkeypatch, lambda: next(values), sent)

    thread._tick()
    thread._tick()
    thread._tick()

    assert [p["agent_artifact_digest"] for p in sent] == [
        "",
        "sha256:" + "a" * 64,
        "sha256:" + "b" * 64,
    ]


def test_string_form_still_flows(monkeypatch):
    """向后兼容：直接传字符串的既有调用方行为不变。"""
    sent = []
    thread = _make_thread(monkeypatch, "sha256:" + "c" * 64, sent)

    thread._tick()
    thread._tick()

    assert [p["agent_artifact_digest"] for p in sent] == [
        "sha256:" + "c" * 64,
        "sha256:" + "c" * 64,
    ]


def test_provider_exception_degrades_to_empty(monkeypatch):
    """提供方异常按空值处理——心跳路径不得因 digest 读取失败判 Agent 死亡。"""

    def broken():
        raise OSError("disk gone")

    sent = []
    thread = _make_thread(monkeypatch, broken, sent)

    thread._tick()

    assert sent[0]["agent_artifact_digest"] == ""
