"""P2-4 / #298 — control handler 须向 call_agent_control 回传 {"ok": true}。"""

from __future__ import annotations

from backend.agent.socketio_client import AgentSocketIOClient


def _one_way_control_handler(data: dict) -> dict:
    """与 main._handle_control 单向命令分支对齐的最小契约。"""
    command = data.get("command", "")
    payload = data.get("payload", {})
    if command == "scan_now":
        if not payload.get("plan_run_id"):
            return {"ok": False, "error": "missing plan_run_id"}
        return {"ok": True}
    if command in {"abort", "reload_config", "archive_now", "backpressure"}:
        return {"ok": True}
    return {"ok": False, "error": f"unknown command: {command}"}


def test_scan_now_handler_returns_ok_ack():
    client = AgentSocketIOClient("http://127.0.0.1:8000", "host-1", "")
    client.set_control_handler(_one_way_control_handler)

    ack = client._control_handler(
        {"command": "scan_now", "payload": {"plan_run_id": 99}},
    )

    assert ack == {"ok": True}


def test_scan_now_missing_plan_run_id_returns_not_ok():
    client = AgentSocketIOClient("http://127.0.0.1:8000", "host-1", "")
    client.set_control_handler(_one_way_control_handler)

    ack = client._control_handler({"command": "scan_now", "payload": {}})

    assert ack == {"ok": False, "error": "missing plan_run_id"}
