"""ADB identity remains available even when the shell channel is closed."""

import subprocess

from backend.agent import device_discovery
from backend.agent.heartbeat_thread import HeartbeatThread


def test_products_survive_discovery_and_failed_shell_probe(monkeypatch):
    rows = [
        ("AHFFNX6930000385", "MLD-NX2", "MLD_NX2"),
        ("ATHTNX6930000005", "MLD-NX2", "MLD_NX2"),
        ("AW8QNX6A06000070", "MLD-NX3", "MLD_NX3"),
        ("ASEBNX6930000003", "sys_mssi_64_cn_armv82", "MLD_NX3"),
        ("AZ6QNX6A06000022", "sys_mssi_64_cn_armv82", "MLD_NX3"),
    ]
    stdout = "List of devices attached\n" + "\n".join(
        f"{serial} device usb:1-5 product:{product} model:{model} device:HNMLD-M1"
        for serial, product, model in rows
    )

    def run(argv, **kwargs):
        if argv[1:] == ["devices", "-l"]:
            return subprocess.CompletedProcess(argv, 0, stdout, "")
        if "shell" in argv:
            return subprocess.CompletedProcess(argv, 1, "", "error: closed")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.delenv("STP_STATIC_DEVICE_SERIALS", raising=False)
    monkeypatch.setattr(device_discovery.subprocess, "run", run)
    monkeypatch.setattr(device_discovery, "list_adb_fork_servers", lambda **kw: [])
    sent = []
    monkeypatch.setattr(
        "backend.agent.heartbeat_thread.send_heartbeat",
        lambda *args, **kwargs: sent.append(kwargs) or {"ok": True},
    )
    thread = HeartbeatThread(
        api_url="http://server", host_id="host-1", adb_path="adb",
        mount_points=[], host_info={}, poll_interval=60,
    )
    thread._tick()
    devices = sent[0]["devices"]
    assert [(d["serial"], d["product"], d["model"]) for d in devices] == rows
    assert all(d["adb_connected"] is False and d["adb_state"] == "offline" for d in devices)
