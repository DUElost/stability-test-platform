"""B6 U8 finite counterexamples; all process signalling is stubbed."""

import pytest

from backend.agent.contracts.device_return import map_step_outcome_to_disarm_result


@pytest.mark.parametrize("code", [124, 125, 143, -15, 137, -9])
@pytest.mark.parametrize("structured", ["confirmed", "failed", "readback_failed"])
def test_platform_interruption_precedes_script_result(code, structured):
    assert map_step_outcome_to_disarm_result(
        started=True, success=False, exit_code=code,
        structured_result=structured, readback="clear", onboarded_family=True,
    ) == "incomplete"


from backend.agent.contracts.device_return import (
    disarm_guard_deadline, freeze_disarm_guard, receive_disarm_evidence,
    probe_result_policy,
)


COMBO = {
    "platform": "TEST-PLATFORM", "model": "TEST_MODEL", "permission_model": "root",
    "autotesttool_apk_sha256": "a" * 64, "script_name": "powercycle_disarm",
    "script_version": "1.0.8", "package_sha256": "b" * 64,
    "report_ref": "test-only://verification",
}
REGISTRY = {"power-cycle": {"verified_device_classes": [COMBO]}}


def entry(raw="clear", mode="disarm", **changes):
    evidence = {**COMBO, "readback": raw, "mode": mode, "success": True,
                "kind": "ok", "boot_completed": "1"}
    evidence.update(changes)
    return {"result": "confirmed", "exit_code": 0, "evidence": evidence}


def policy(item, **changes):
    facts = dict(platform=COMBO["platform"], model=COMBO["model"],
                 script_name=COMBO["script_name"], script_version=COMBO["script_version"],
                 package_sha256=COMBO["package_sha256"], token_valid=True, onboarded=REGISTRY)
    facts.update(changes)
    return probe_result_policy("power-cycle", item, **facts)


def test_exact_test_combination_can_confirm_raw_clear():
    assert policy(entry())["result"] == "confirmed"
    assert policy(entry(mode="probe"))["result"] == "confirmed"


@pytest.mark.parametrize("mode", ["probe", "disarm"])
@pytest.mark.parametrize("raw", ["absent", "not_installed", "footprint", "readback_failed"])
def test_raw_absence_or_failure_cannot_confirm(mode, raw):
    result = policy(entry(raw=raw, mode=mode))["result"]
    assert result != "confirmed"
    if mode == "probe" or raw in ("footprint", "readback_failed"):
        assert result == "failed"


@pytest.mark.parametrize("field,value", [
    ("permission_model", "unknown"), ("permission_model", "non-root"),
    ("autotesttool_apk_sha256", ""), ("autotesttool_apk_sha256", "x" * 64),
    ("package_sha256", "c" * 64), ("boot_completed", "0"),
    ("boot_completed", None), ("script_version", "1.0.7"),
])
def test_forged_formal_result_cannot_bypass_exact_match(field, value):
    got = policy(entry(**{field: value, "verified_category": True}))
    assert not got["trusted"]
    assert got["result"] != "confirmed"


@pytest.mark.parametrize("facts", [{"platform": "OTHER"}, {"model": "TEST-MODEL"},
                                  {"token_valid": False}, {"package_sha256": None}])
def test_caller_facts_are_authoritative(facts):
    assert policy(entry(), **facts)["result"] != "confirmed"


STEP = {"step_id": "disarm", "action": "script:powercycle_disarm", "version": "1.0.8",
        "timeout_seconds": 660, "package_sha256": "b" * 64}


def evidence(seq, phase="queue", progress=0):
    return {"step_id": "disarm", "seq": seq, "phase": phase, "progress_seq": progress,
            "script_name": "powercycle_disarm", "script_version": "1.0.8", "package_sha256": "b" * 64}


def guard():
    return freeze_disarm_guard([STEP], now=0, abort_at=0, fencing_token="test-token", agent_instance_id="test-instance")


def test_eleven_jobs_queue_for_6600_seconds_and_keep_independent_660_execution():
    guards = [guard() for _ in range(11)]
    for seq, at in enumerate(range(0, 6601, 15)):
        guards = [receive_disarm_evidence(g, evidence(seq), now=at, via_coordinator=True) for g in guards]
        assert all(disarm_guard_deadline(g, now=at, owner_valid=True, execution_heartbeat_at=None) for g in guards)
    for g in guards:
        executing = receive_disarm_evidence(g, evidence(1000, "execute", 1), now=6600, via_coordinator=True)
        assert executing["step_deadlines"]["disarm"] == 7260
        assert executing["job_deadline"] == 7860
        assert disarm_guard_deadline(executing, now=6600, owner_valid=True, execution_heartbeat_at=6600)


def test_replays_retry_and_clock_regression_cannot_extend_deadlines():
    g = receive_disarm_evidence(guard(), evidence(1), now=0, via_coordinator=True)
    repeat = receive_disarm_evidence(g, evidence(1), now=40, via_coordinator=True)
    assert repeat["queue_seen_at"] == 0
    assert disarm_guard_deadline(repeat, now=61, owner_valid=True, execution_heartbeat_at=61) is None
    executing = receive_disarm_evidence(g, evidence(2, "execute", 1), now=30, via_coordinator=True)
    retried = receive_disarm_evidence(executing, evidence(3), now=40, via_coordinator=True)
    retried = receive_disarm_evidence(retried, evidence(4, "execute", 2), now=50, via_coordinator=True)
    assert retried["step_deadlines"]["disarm"] == 690
    regressed = receive_disarm_evidence(retried, evidence(5), now=49, via_coordinator=True)
    assert regressed["reason"] == "clock_regressed"
    assert disarm_guard_deadline(regressed, now=51, owner_valid=True, execution_heartbeat_at=50) is None


def test_extend_only_and_execute_heartbeat_without_progress_do_not_protect():
    g = receive_disarm_evidence(guard(), evidence(1), now=0, via_coordinator=False)
    assert disarm_guard_deadline(g, now=1, owner_valid=True, execution_heartbeat_at=1) is None
    g = receive_disarm_evidence(guard(), evidence(1, "execute"), now=0, via_coordinator=True)
    assert disarm_guard_deadline(g, now=1, owner_valid=True, execution_heartbeat_at=1) is None


def test_coordinator_60s_configuration_still_delivers_every_30s(monkeypatch):
    from backend.agent.coordinator import HostRunCoordinator
    from backend.agent import coordinator as module
    from types import SimpleNamespace

    clock = [0.0]
    received = []
    coordinator = HostRunCoordinator("http://test-only", "test-host", "test-instance")
    coordinator._interval = 60
    coordinator.register_plan_run_host(1, 1)
    coordinator.publish_disarm_evidence(1, {"phase": "queue", "seq": 1})

    class FiniteWait:
        def wait(self, duration):
            clock[0] += duration
        def clear(self):
            pass
    coordinator._wake_event = FiniteWait()
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    def post(*args, **kwargs):
        assert kwargs["json"]["jobs"][0]["disarm_evidence"]["phase"] == "queue"
        received.append(clock[0])
        clock[0] += 10  # simulated HTTP duration, included in the pacing budget
        if len(received) == 4:
            coordinator._stop_event.set()
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"data": {"disarm_protocol": 1}})
    monkeypatch.setattr(module.requests, "post", post)
    coordinator._loop()
    assert len(received) == 4
    assert received[0] <= 30
    assert all(b-a <= 30 for a, b in zip(received, received[1:], strict=False))


def interrupted_engine_payload(monkeypatch, tmp_path, kind, family="sleep"):
    """Real lifecycle→script action→pump→runner; only process/IO is replaced."""
    import io
    from types import SimpleNamespace
    from backend.agent import pipeline_engine as module, pipeline_runner
    from backend.agent.operation_scheduler import OperationScheduler

    setup, finish = ("sleep_setup", "sleep_finish") if family == "sleep" else ("powercycle_setup", "powercycle_disarm")
    pipeline = {"lifecycle": {"init": [{"step_id": "setup", "action": f"script:{setup}"}],
        "teardown": [{"step_id": "disarm", "action": f"script:{finish}", "version": "1.0.8",
                      "timeout_seconds": 1 if kind == "wall_clock" else 660, "stall_seconds": 1}]}}
    entry = SimpleNamespace(script_type="python", package_sha256="b"*64)
    registry = SimpleNamespace(resolve=lambda *a: entry)
    engine = module.PipelineEngine(SimpleNamespace(adb_path="stub-adb"), "test-serial", 1,
        log_dir=str(tmp_path), script_registry=registry, nfs_root="", operation_scheduler=OperationScheduler(max_concurrent=1), device_id=1)
    monkeypatch.setattr(engine, "_verify_device_lease", lambda: None)
    original_action = engine._run_script_action
    monkeypatch.setattr(engine, "_run_script_action", lambda ctx, step:
        module.StepResult(success=True, exit_code=0) if ctx.phase == "init" else original_action(ctx, step))
    monkeypatch.setattr(module, "resolve_script_path", lambda e: SimpleNamespace(path="test-only.py", cwd=str(tmp_path), source="package"))
    monkeypatch.setattr(module, "inject_required_tools", lambda *a: None)
    monkeypatch.setattr(module, "_remember_process_group", lambda proc: None)
    monkeypatch.setattr(module, "_popen_isolation_kwargs", lambda: {})
    monkeypatch.setattr(module, "_IS_WINDOWS", True)  # StringIO has no OS pipe descriptor
    class Process:
        pid = 99999999
        returncode = 1 if kind == "script_fail" else None
        stdout = io.StringIO('{"success":true,"disarm_result":"confirmed","readback":"clear"}')
        stderr = io.StringIO("")
        def poll(self):
            return self.returncode
        def wait(self, timeout=None):
            if self.returncode is None:
                self.returncode = -15
            return self.returncode
    monkeypatch.setattr(module.subprocess, "Popen", lambda *a, **k: Process())
    monkeypatch.setattr(module, "_terminate_process_tree", lambda proc, **k: setattr(proc, "returncode", -15))
    real_pump = module._pump_process
    clock = iter(range(0, 20, 2))  # finite virtual clock, no host signals or long sleep
    monkeypatch.setattr(module, "_pump_process", lambda *a, **k: real_pump(*a, **k, now=lambda: next(clock)))
    monkeypatch.setattr(pipeline_runner, "PipelineEngine", lambda **kwargs: engine)
    monkeypatch.setattr(pipeline_runner, "get_run_log_dir", lambda _: str(tmp_path))
    result = pipeline_runner.execute_pipeline_run(pipeline, 1, "test-serial", engine._adb,
        "http://test-only", "test-host", operation_scheduler=engine._scheduler, device_id=1)
    return pipeline, result


@pytest.mark.parametrize("kind,code", [("wall_clock", 124), ("stall", 125)])
def test_controlled_pump_interruption_survives_real_engine_and_runner(monkeypatch, tmp_path, kind, code):
    _, result = interrupted_engine_payload(monkeypatch, tmp_path, kind)
    assert result["status"] == "FINISHED"
    assert result["disarm_results"]["sleep"]["result"] == "incomplete"
    assert result["disarm_results"]["sleep"]["exit_code"] == code


@pytest.mark.parametrize("entry_point,status,use_trace,ignore", [
    ("complete", 200, True, False), ("late", 200, False, True),
    ("discharge", 422, False, False), ("init_check", 422, False, False),
])
def test_four_entry_shared_policy_including_future_init(entry_point, status, use_trace, ignore):
    from backend.agent.contracts.device_return import probe_entry_response
    item = entry(permission_model="unknown")
    decision = probe_entry_response("power-cycle", item, policy(item), entry_point=entry_point)
    assert decision["status_code"] == status
    assert decision["use_trace"] is use_trace
    assert decision["ignore"] is ignore
    assert decision["code"] == "UNVERIFIED_PROBE_RESULT"
    assert decision["family"] == "power-cycle"
    assert decision["expected"] and decision["observed"]


@pytest.mark.parametrize("changed", [None, "platform", "model", "permission_model", "autotesttool_apk_sha256", "package_sha256", "boot_completed"])
def test_agent_aggregation_recomputes_raw_output_from_test_combination(monkeypatch, changed):
    import json
    from types import SimpleNamespace
    from backend.agent.contracts.device_return import ONBOARDED
    from backend.agent.tests.test_device_return_u1_3726 import _bare_engine

    combination = dict(COMBO)
    monkeypatch.setitem(ONBOARDED, "power-cycle", {**ONBOARDED["power-cycle"], "verified_device_classes": [combination]})
    engine = _bare_engine()
    engine._script_registry = SimpleNamespace(resolve=lambda *a: SimpleNamespace(package_sha256="b"*64))
    platform = "OTHER" if changed == "platform" else COMBO["platform"]
    model = "TEST-MODEL" if changed == "model" else COMBO["model"]
    monkeypatch.setattr(engine, "_disarm_device_identity", lambda: (platform, model))
    evidence = {"kind": "ok", "boot_completed": "1", "permission_model": "root", "autotesttool_apk_sha256": "a"*64}
    if changed in evidence:
        evidence[changed] = "unknown" if changed == "permission_model" else "different"
    if changed == "package_sha256":
        engine._script_registry = SimpleNamespace(resolve=lambda *a: SimpleNamespace(package_sha256="c"*64))
    engine._record_disarm_for_step(STEP, started=True, success=True, exit_code=0,
        output=json.dumps({"success": True, "disarm_result": "confirmed", "mode": "disarm", "readback": "clear", "evidence": evidence}))
    reported = engine._disarm_results["power-cycle"]
    assert reported["result"] == ("confirmed" if changed is None else "executed_unverified")
    assert reported["evidence"]["script_name"] == "powercycle_disarm"
    assert reported["evidence"]["script_version"] == "1.0.8"


@pytest.mark.parametrize("queued_seconds", [61, 6600, 7201])
def test_eleven_real_scheduler_waiters_share_one_permit_and_keep_execution_window(monkeypatch, queued_seconds):
    import threading
    from types import SimpleNamespace
    from backend.agent import pipeline_engine as module
    from backend.agent.operation_scheduler import OperationScheduler
    from backend.agent.coordinator import HostRunCoordinator

    clock = [0.0]
    scheduler = OperationScheduler(max_concurrent=1)
    holder = scheduler.acquire(999)
    ready = threading.Event()
    lock = threading.Lock()
    waiting = set()
    windows = []
    errors = []
    results = []
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])

    class SharedScheduler:
        def acquire(self, device_id, timeout):
            with lock:
                waiting.add(device_id)
                if len(waiting) == 11:
                    clock[0] = queued_seconds
                    holder.release()
                    ready.set()
            assert ready.wait(2), "finite barrier: all eleven workers must reach permit wait"
            return scheduler.acquire(device_id, timeout=timeout)
    coord = HostRunCoordinator("http://unused-test-only", "test-host", "test-instance")
    engines = []
    for n in range(11):
        engine = module.PipelineEngine(SimpleNamespace(adb_path="stub"), f"test-{n}", n+1,
            nfs_root="", operation_scheduler=SharedScheduler(), coordinator=coord, device_id=n+1)
        engine._footprint_families = {"sleep"}
        def action(ctx, step):
            with lock:
                windows.append(step["timeout_seconds"])
            return module.StepResult(success=True, exit_code=0)
        monkeypatch.setattr(engine, "_run_script_action", action)
        engines.append(engine)
    def worker(engine):
        try:
            result = engine._execute_teardown_best_effort([
                {"step_id": "sleep-finish", "action": "script:sleep_finish", "timeout_seconds": 660}])
            with lock:
                results.append(result.metadata["disarm_results"]["sleep"]["result"])
        except Exception as error:
            with lock:
                errors.append(error)
    threads = [threading.Thread(target=worker, args=(engine,)) for engine in engines]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(3)
    assert all(not thread.is_alive() for thread in threads)
    assert not errors, errors
    assert len(results) == 11
    if queued_seconds <= 7200:
        assert len(windows) == 11
        assert all(659 < window <= 660 for window in windows)
        assert set(results) == {"executed_unverified"}
    else:
        assert not windows
        assert set(results) == {"incomplete"}


def test_permit_starts_without_control_plane_ack_and_terminal_is_durable(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from backend.agent.pipeline_engine import PipelineEngine, StepResult
    from backend.agent.operation_scheduler import OperationScheduler
    from backend.agent import api_client
    from backend.agent.registry.local_db import LocalDB
    import requests

    store = LocalDB()
    store.initialize(str(tmp_path / "agent.sqlite"))
    store.save_active_job(1, 1, "test-token", "test-serial")
    engine = PipelineEngine(SimpleNamespace(adb_path="stub"), "test-serial", 1, nfs_root="",
        api_url="http://unreachable-test-only", fencing_token="test-token", local_db=store,
        operation_scheduler=OperationScheduler(max_concurrent=1), device_id=1)
    engine._footprint_families = {"sleep"}
    calls = []
    def unavailable(*args, **kwargs):
        calls.append("http")
        raise requests.ConnectionError("test-only unreachable")
    monkeypatch.setattr(api_client, "_post_with_retry", unavailable)
    # v1.7.13 retains one bounded lease probe; no phase acknowledgement.
    monkeypatch.setattr(requests, "post", unavailable)
    def action(ctx, step):
        assert calls == ["http"]
        calls.append("disarm")
        return StepResult(success=True, exit_code=0)
    monkeypatch.setattr(engine, "_run_script_action", action)
    result = engine._execute_teardown_best_effort([{"step_id": "finish", "action": "script:sleep_finish", "timeout_seconds": 660}])
    api_client.complete_job("http://unreachable-test-only", 1,
        {"status": "FINISHED", "disarm_results": result.metadata["disarm_results"]}, "test-token", local_db=store)
    assert calls == ["http", "disarm", "http"]
    assert store.get_pending_terminals()[0]["payload"]["disarm_results"]["sleep"]["result"] == "executed_unverified"
    store.close()


def test_localdb_preserves_deadlines_and_sequences_across_re_registration_and_restart(tmp_path):
    from backend.agent.registry.local_db import LocalDB
    path = str(tmp_path / "agent.sqlite")
    first = LocalDB()
    first.initialize(path)
    first.save_active_job(1, 1, "old-test-token")
    state = {"seq": 7, "guard": guard(), "evidence": evidence(7)}
    first.save_disarm_state(1, state)
    first.save_active_job(1, 1, "rotated-test-token")
    first.close()
    second = LocalDB()
    second.initialize(path)
    assert second.load_disarm_state(1) == state
    second.close()


def test_old_controller_uses_v10_without_inventing_queue_protection(monkeypatch):
    from types import SimpleNamespace
    from backend.agent.pipeline_engine import PipelineEngine, StepResult
    from backend.agent.coordinator import HostRunCoordinator
    from backend.agent.operation_scheduler import OperationScheduler
    coordinator = HostRunCoordinator("http://test-only", "test-host", "test-instance")
    coordinator.disarm_protocol = 0
    engine = PipelineEngine(SimpleNamespace(adb_path="stub"), "test-serial", 1, nfs_root="",
        coordinator=coordinator, operation_scheduler=OperationScheduler(max_concurrent=1), device_id=1)
    engine._footprint_families = {"sleep"}
    monkeypatch.setattr(engine, "_run_script_action", lambda *a: StepResult(success=True, exit_code=0))
    engine._execute_teardown_best_effort([{"step_id": "finish", "action": "script:sleep_finish", "timeout_seconds": 660}])
    assert engine._disarm_tail is None
    assert coordinator.register_job(1).disarm_evidence is None


from backend.agent.tests.disarm_real_combinations_3752 import COMBINATIONS, CHANGES, aggregate_script_output


@pytest.mark.parametrize("model,permission", COMBINATIONS)
@pytest.mark.parametrize("changed", CHANGES)
def test_registered_real_combinations_recompute_actual_script_output(monkeypatch, model, permission, changed):
    reported, _ = aggregate_script_output(monkeypatch, model, permission, changed)
    assert reported["result"] == ("confirmed" if changed is None else "executed_unverified")


def test_old_controller_discovered_during_tail_reverts_to_v10(monkeypatch, caplog):
    from types import SimpleNamespace
    from backend.agent.tests.test_device_return_u1_3726 import _bare_engine
    engine = _bare_engine()
    view = SimpleNamespace(disarm_evidence={"phase": "queue"})
    coord = SimpleNamespace(disarm_protocol=None, guard_for_job=lambda _: None,
                            register_job=lambda _: view,
                            publish_disarm_evidence=lambda _, e: setattr(view, "disarm_evidence", e))
    engine._coordinator = coord
    engine._begin_disarm_tail([STEP], disarm_only=True)
    assert engine._disarm_tail is not None
    coord.disarm_protocol = 0
    engine._refresh_disarm_guard()
    assert engine._disarm_tail is None
    assert view.disarm_evidence is None
    assert "disarm_protocol_unavailable" in caplog.text


def test_registry_contains_exactly_four_planner_confirmed_combinations():
    from backend.agent.contracts.device_return import ONBOARDED
    from backend.agent.tests.disarm_real_combinations_3752 import APK, PACKAGE
    combos = ONBOARDED["power-cycle"]["verified_device_classes"]
    assert len(combos) == 4
    assert {(c["model"], c["permission_model"]) for c in combos} == set(COMBINATIONS)
    for combo in combos:
        assert combo["platform"] == "MTK"
        assert combo["autotesttool_apk_sha256"] == APK[combo["model"]]
        assert combo["package_sha256"] == PACKAGE
        assert combo["script_name"] == "powercycle_disarm" and combo["script_version"] == "1.0.8"
        assert len(combo["report_ref"]) == 3
        assert "6312583571" in combo["report_ref"][0]
        assert combo["report_ref"][1].endswith("2026-10-21-b6-u5-realdevice-108.md")
        assert "6320738260" in combo["report_ref"][2]


@pytest.mark.parametrize("wall", [None, 0, -1, True, "660"])
def test_invalid_legacy_wall_is_fixed_300(wall):
    step = {**STEP, "timeout_seconds": wall}
    g = freeze_disarm_guard([step], now=10, abort_at=0, fencing_token="t", agent_instance_id="i")
    assert g["job_deadline"] == 7500
    assert g["step_budgets"]["disarm"]["seconds"] == 300
    assert g["step_budgets"]["disarm"]["reason"] == "legacy_missing_wall_clock"


def test_job_cap_and_each_step_retry_deadline_remain_absolute():
    steps = [{**STEP, "step_id": str(n), "timeout_seconds": 50000} for n in range(2)]
    g = freeze_disarm_guard(steps, now=0, abort_at=0, fencing_token="t", agent_instance_id="i")
    assert g["job_deadline"] == 86400
    first = {**evidence(1, "execute", 1), "step_id": "0"}
    g = receive_disarm_evidence(g, first, now=50000, via_coordinator=True)
    assert g["step_deadlines"]["0"] == 86400
    g = receive_disarm_evidence(g, {**first, "seq": 2, "progress_seq": 2, "timestamp": 10**20}, now=86399, via_coordinator=True)
    assert g["step_deadlines"]["0"] == 86400
    assert disarm_guard_deadline(g, now=86400, owner_valid=True, execution_heartbeat_at=86400) is None
    for seq in (2, 1, -1):
        replay = receive_disarm_evidence(g, {**first, "seq": seq}, now=86401, via_coordinator=True)
        assert replay["step_deadlines"] == g["step_deadlines"]
        assert replay["job_deadline"] == g["job_deadline"]


def test_exhausted_local_step_never_runs_another_attempt(monkeypatch):
    from backend.agent.tests.test_device_return_u1_3726 import _bare_engine
    from backend.agent import pipeline_engine as module
    engine = _bare_engine()
    engine._coordinator = None
    monkeypatch.setattr(module.time, "monotonic", lambda: 100)
    engine._disarm_tail = {"deadline": 200, "step_deadlines": {"disarm": 99}}
    monkeypatch.setattr(engine, "_run_script_action", lambda *a: pytest.fail("exhausted step executed"))
    assert engine._run_step_with_retry("teardown", STEP, disarm_step=True) is False
    assert engine._disarm_results["power-cycle"]["result"] == "incomplete"


def test_restarted_tail_can_negotiate_old_controller(monkeypatch):
    from types import SimpleNamespace
    from backend.agent.tests.test_device_return_u1_3726 import _bare_engine
    engine = _bare_engine()
    coord = SimpleNamespace(disarm_protocol=None, guard_for_job=lambda _: None, publish_disarm_evidence=lambda *a: None)
    coord._tick = lambda: setattr(coord, "disarm_protocol", 0)
    engine._coordinator = coord
    engine._local_db = SimpleNamespace(load_disarm_state=lambda _: {"seq": 7, "evidence": {"phase": "queue"}})
    engine._begin_disarm_tail([STEP], disarm_only=True)
    assert engine._disarm_tail is None


@pytest.mark.parametrize("statuses", [[409], [200, 409], [200, 200]])
def test_tail_checks_lease_once_before_each_disarm_and_before_execute_evidence(monkeypatch, statuses):
    """#3723 v1.7.13: permit ownership is not proof of device lease ownership."""
    from types import SimpleNamespace
    from backend.agent import pipeline_engine as module
    from backend.agent.coordinator import HostRunCoordinator
    from backend.agent.operation_scheduler import OperationScheduler
    coord = HostRunCoordinator("http://test-only", "test-host", "test-instance")
    coord.disarm_protocol = 1
    events, started = [], []
    monkeypatch.setattr(coord, "publish_disarm_evidence", lambda _, e: events.append(("phase", e["phase"], e["step_id"])) if e else None)
    engine = module.PipelineEngine(SimpleNamespace(adb_path="stub"), "test-only", 1, nfs_root="",
        api_url="http://test-only", fencing_token="test-token", coordinator=coord,
        operation_scheduler=OperationScheduler(max_concurrent=1), device_id=1)
    engine._footprint_families = {"sleep", "mtbf"} if len(statuses)==2 else {"sleep"}
    monkeypatch.setattr(engine, "_report_step_trace_mq", lambda *a, **k: None)
    replies = iter(statuses)
    def post(url, **kwargs):
        assert url.endswith("/extend_lock")
        assert kwargs["timeout"] == 5
        reply = next(replies)  # finite: an extra probe is a test failure
        events.append(("probe", reply))
        return SimpleNamespace(status_code=reply)
    monkeypatch.setattr(module.requests, "post", post)
    def action(ctx, step):
        started.append(step["step_id"])
        events.append(("spawn", step["step_id"]))
        return module.StepResult(success=True, exit_code=0)
    monkeypatch.setattr(engine, "_run_script_action", action)
    steps = [{"step_id": family, "action": f"script:{family}_finish", "timeout_seconds": 660}
             for family in ("sleep", "mtbf")[:len(statuses)]]
    result = engine._execute_teardown_best_effort(steps)
    assert [e[1] for e in events if e[0]=="probe"] == statuses
    probe_positions = [i for i, event in enumerate(events) if event[0] == "probe"]
    for index, (family, status) in enumerate(zip(("sleep", "mtbf"), statuses, strict=False)):
        executed = ("phase", "execute", family)
        entry = result.metadata["disarm_results"][family]
        if status == 409:
            assert family not in started
            assert executed not in events
            assert entry["result"] == "not_executed"
            assert entry["reason"] == "lease_lost"
        else:
            assert family in started
            assert events.count(executed) == 1
            assert probe_positions[index] < events.index(executed) < events.index(("spawn", family))
            assert entry["result"] == "executed_unverified"


def test_tail_unreachable_control_plane_starts_after_one_five_second_probe(monkeypatch):
    from types import SimpleNamespace
    import requests
    from backend.agent import pipeline_engine as module
    from backend.agent.coordinator import HostRunCoordinator
    from backend.agent.operation_scheduler import OperationScheduler
    clock, probes, phases = [0.0], [], []
    coord = HostRunCoordinator("http://test-only", "test-host", "test-instance")
    coord.disarm_protocol = 1
    monkeypatch.setattr(coord, "publish_disarm_evidence", lambda _, e: phases.append((e["phase"], clock[0])) if e else None)
    monkeypatch.setattr(coord, "_tick", lambda: pytest.fail("must not wait for stage acknowledgement"))
    engine = module.PipelineEngine(SimpleNamespace(adb_path="stub"), "test-only", 1, nfs_root="",
        api_url="http://test-only", fencing_token="test-token", coordinator=coord,
        operation_scheduler=OperationScheduler(max_concurrent=1), device_id=1)
    engine._footprint_families = {"sleep"}
    monkeypatch.setattr(engine, "_report_step_trace_mq", lambda *a, **k: None)
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    def timeout(url, **kwargs):
        assert url.endswith("/extend_lock") and kwargs["timeout"] == 5
        probes.append(kwargs["timeout"])
        clock[0] += kwargs["timeout"]
        raise requests.Timeout("test-only five-second timeout")
    monkeypatch.setattr(module.requests, "post", timeout)
    def action(ctx, step):
        assert clock[0] == 5
        assert probes == [5]
        assert phases[-1] == ("execute", 5)
        return module.StepResult(success=True, exit_code=0)
    monkeypatch.setattr(engine, "_run_script_action", action)
    result = engine._execute_teardown_best_effort([{"step_id": "finish", "action": "script:sleep_finish", "timeout_seconds": 660}])
    assert probes == [5]
    assert result.metadata["disarm_results"]["sleep"]["result"] == "executed_unverified"
