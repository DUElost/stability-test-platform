"""Agent heartbeat catalog-version tests."""

from backend.agent.heartbeat import send_heartbeat
from backend.agent.heartbeat_thread import HeartbeatThread


class FakeResponse:
    def raise_for_status(self):
        return None

    def json(self):
        return {"ok": True, "script_catalog_outdated": True}


def test_send_heartbeat_includes_catalog_versions(monkeypatch):
    captured = {}

    def fake_post(url, json, headers=None, timeout=5):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return FakeResponse()

    monkeypatch.setenv("AGENT_SECRET", "secret")
    monkeypatch.setattr("backend.agent.heartbeat.requests.post", fake_post)
    monkeypatch.setattr("backend.agent.system_monitor.collect_system_stats", lambda: {"cpu": 1})

    send_heartbeat(
        "http://server",
        "host-1",
        tool_catalog_version="tool-v",
        script_catalog_version="script-v",
    )

    assert captured["url"] == "http://server/api/v1/heartbeat"
    assert captured["headers"] == {"x-agent-secret": "secret"}
    assert captured["json"]["tool_catalog_version"] == "tool-v"
    assert captured["json"]["script_catalog_version"] == "script-v"


def test_heartbeat_thread_refreshes_scripts_when_server_reports_outdated(monkeypatch):
    refreshed = []
    sent_payloads = []

    def fake_send_heartbeat(*args, **kwargs):
        sent_payloads.append(kwargs)
        return {"ok": True, "script_catalog_outdated": True}

    monkeypatch.setattr("backend.agent.heartbeat_thread.send_heartbeat", fake_send_heartbeat)
    monkeypatch.setattr("backend.agent.heartbeat_thread.device_discovery.discover_devices", lambda adb: [])

    thread = HeartbeatThread(
        api_url="http://server",
        host_id="host-1",
        adb_path="adb",
        mount_points=[],
        host_info={},
        poll_interval=60,
        catalog_versions=lambda: {
            "tool_catalog_version": "tool-v",
            "script_catalog_version": "script-v",
        },
        on_scripts_outdated=lambda: refreshed.append(True),
    )

    thread._tick()

    assert sent_payloads[0]["tool_catalog_version"] == "tool-v"
    assert sent_payloads[0]["script_catalog_version"] == "script-v"
    assert refreshed == [True]
