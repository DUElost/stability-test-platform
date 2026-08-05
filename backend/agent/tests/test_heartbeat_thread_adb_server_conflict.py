"""#160: HeartbeatThread 检测多 ADB server 冲突并透传 health，支持可选自动修复。"""

from backend.agent.heartbeat_thread import HeartbeatThread


def _make_thread(monkeypatch, *, conflict: bool = True, active_job_count: int = 0):
    sent_payloads = {}
    cap_kwargs = {}

    def fake_send_heartbeat(*args, **kwargs):
        sent_payloads["kwargs"] = kwargs
        return {"ok": True}

    def fake_compute_capacity(**kwargs):
        cap_kwargs.update(kwargs)
        if kwargs.get("adb_server_conflict"):
            health = {"status": "DEGRADED", "reasons": ["adb_multiple_servers"]}
        else:
            health = {"status": "HEALTHY", "reasons": []}
        return {"capacity": {"effective_slots": 10}, "health": health}

    servers = [
        {"pid": 111, "uid": 0, "port": 5039, "cmdline": "adb -L tcp:5039 fork-server server"},
        {"pid": 222, "uid": 0, "port": 5037, "cmdline": "adb -L tcp:5037 fork-server server"},
    ]
    if not conflict:
        servers = [servers[1]]

    monkeypatch.setattr("backend.agent.heartbeat_thread.send_heartbeat", fake_send_heartbeat)
    monkeypatch.setattr(
        "backend.agent.heartbeat_thread.device_discovery.discover_devices",
        lambda adb: [],
    )
    monkeypatch.setattr(
        "backend.agent.heartbeat_thread.device_discovery.get_adb_server_port",
        lambda: 5037,
    )
    monkeypatch.setattr(
        "backend.agent.heartbeat_thread.device_discovery.list_adb_fork_servers",
        lambda: servers,
    )
    monkeypatch.setattr(
        "backend.agent.capacity_reporter.compute_capacity",
        fake_compute_capacity,
    )

    thread = HeartbeatThread(
        api_url="http://server",
        host_id="host-1",
        adb_path="adb",
        mount_points=[],
        host_info={},
        poll_interval=60,
        get_active_job_count=lambda: active_job_count,
    )
    return thread, sent_payloads, cap_kwargs


def test_tick_detects_conflict_and_passes_to_capacity(monkeypatch):
    thread, sent_payloads, cap_kwargs = _make_thread(monkeypatch, conflict=True)

    thread._tick()

    assert cap_kwargs["adb_server_conflict"] is True
    assert sent_payloads["kwargs"]["health"]["status"] == "DEGRADED"
    assert sent_payloads["kwargs"]["health"]["reasons"] == ["adb_multiple_servers"]


def test_tick_no_conflict_passes_false(monkeypatch):
    thread, sent_payloads, cap_kwargs = _make_thread(monkeypatch, conflict=False)

    thread._tick()

    assert cap_kwargs["adb_server_conflict"] is False
    assert sent_payloads["kwargs"]["health"]["status"] == "HEALTHY"


def test_auto_repair_runs_when_enabled_idle_and_cooldown(monkeypatch):
    monkeypatch.setenv("STP_ADB_AUTO_REPAIR", "1")
    repairs = []

    def fake_repair(adb_path):
        repairs.append(adb_path)
        return {"port": 5037, "killed": [{"pid": 111, "port": 5039}], "started": True}

    monkeypatch.setattr(
        "backend.agent.heartbeat_thread.device_discovery.ensure_single_adb_server",
        fake_repair,
    )
    thread, _sent, _cap = _make_thread(monkeypatch, conflict=True, active_job_count=0)

    thread._tick()
    thread._tick()

    # 冷却 300s：同一秒内第二次 tick 不应重复修复
    assert repairs == ["adb"]


def test_auto_repair_skips_when_jobs_active(monkeypatch):
    monkeypatch.setenv("STP_ADB_AUTO_REPAIR", "1")
    monkeypatch.setattr(
        "backend.agent.heartbeat_thread.device_discovery.ensure_single_adb_server",
        lambda adb_path: {"port": 5037, "killed": [], "started": True},
    )
    thread, _sent, _cap = _make_thread(monkeypatch, conflict=True, active_job_count=1)

    thread._tick()

    # 有活动 Job 时只告警，不杀 server
    assert _cap["adb_server_conflict"] is True
