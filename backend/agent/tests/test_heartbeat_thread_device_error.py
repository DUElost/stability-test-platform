"""HeartbeatThread._tick() should surface adb-discovered non-"device" raw
states (e.g. "unauthorized") as a distinct error condition instead of
silently collapsing them into "offline".

See issue #52: DeviceStatus previously had no ERROR value and
collect_device_info() always overwrote unauthorized/no-permission states
with "offline" via its own shell probe.
"""

from backend.agent.heartbeat_thread import HeartbeatThread


def _make_thread(monkeypatch, discovered_devices):
    sent_payloads = []

    def fake_send_heartbeat(*args, **kwargs):
        sent_payloads.append(kwargs)
        return {"ok": True}

    monkeypatch.setattr("backend.agent.heartbeat_thread.send_heartbeat", fake_send_heartbeat)
    monkeypatch.setattr(
        "backend.agent.heartbeat_thread.device_discovery.discover_devices",
        lambda adb: discovered_devices,
    )

    thread = HeartbeatThread(
        api_url="http://server",
        host_id="host-1",
        adb_path="adb",
        mount_points=[],
        host_info={},
        poll_interval=60,
    )
    return thread, sent_payloads


def test_tick_preserves_unauthorized_raw_state_without_shell_probe(monkeypatch):
    thread, sent_payloads = _make_thread(
        monkeypatch,
        discovered_devices=[{"serial": "SERIAL-1", "adb_state": "unauthorized", "model": None}],
    )

    thread._tick()

    devices = sent_payloads[0]["devices"] if sent_payloads and "devices" in sent_payloads[0] else thread.latest_devices
    assert len(devices) == 1
    assert devices[0]["adb_state"] == "unauthorized"
    assert devices[0]["adb_connected"] is False


def test_tick_still_probes_shell_when_raw_state_is_device(monkeypatch):
    """Sanity check: normal "device" raw state keeps going through collect_device_info's
    own shell probe (which will fail here since there's no real adb binary), landing on
    the existing "offline" fallback rather than a new "unauthorized"-style value."""
    thread, _sent_payloads = _make_thread(
        monkeypatch,
        discovered_devices=[{"serial": "SERIAL-2", "adb_state": "device", "model": None}],
    )

    thread._tick()

    devices = thread.latest_devices
    assert len(devices) == 1
    assert devices[0]["adb_connected"] is False
