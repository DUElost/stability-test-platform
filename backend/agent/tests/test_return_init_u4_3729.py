"""Real frozen script → real Engine/pump → dedicated init report (§3.5/§4.1 U4)."""
import json

import pytest
import requests

from backend.agent.pipeline_engine import StepResult
from backend.agent import pipeline_runner
from backend.agent.tests.return_frozen_3729 import FrozenReturnDevice, PACKAGE


@pytest.fixture
def device_factory(monkeypatch, tmp_path, real_return_checks):
    devices = []

    def make(**kwargs):
        device = FrozenReturnDevice(monkeypatch, tmp_path, **kwargs)
        devices.append(device)
        return device
    yield make
    for device in devices:
        device.close()


@pytest.mark.parametrize("kind,trusted,modes,value,success", [
    ("clear", True, ["probe"], "confirmed", True),
    ("footprint", True, ["probe", "disarm"], "confirmed", True),
    ("absent", True, ["probe", "disarm"], "confirmed", True),
    ("clear", False, ["probe", "disarm"], "executed_unverified", True),
    ("not_installed", True, ["probe", "disarm"], "executed_unverified", True),
    ("denied", True, ["probe", "disarm"], "failed", False),
])
def test_frozen_init_paths_and_terminal_separation(device_factory, kind, trusted, modes, value, success):
    device = device_factory(kind=kind, trusted=trusted)
    result = device.execute()
    assert result.success is success, result.error_message
    assert device.modes == modes
    assert device.scheduler.held == 0
    assert len(device.reports) == 1
    entry = device.reports[0]["family_results"]["power-cycle"]
    assert entry["result"] == value
    assert entry["step_id"] == "return_check.power-cycle" and entry["source"] == "init_check"
    assert entry["evidence"]["script_name"] == "powercycle_disarm"
    assert entry["evidence"]["script_version"] == "1.0.8"
    assert entry["evidence"]["package_sha256"] == PACKAGE
    assert not result.metadata.get("disarm_results")
    traces = [c.kwargs for c in device.mq.send_step_trace.call_args_list]
    assert traces and all(t["stage"] == "init" and t["step_id"] == "return_check.power-cycle" for t in traces)
    # #3723 §3.5 v1.7.18.7: child exit 1 must not become a job step failure.
    terminal_event = "COMPLETED" if success else "FAILED"
    assert [(t["event_type"], t["status"]) for t in traces] == [
        ("STARTED", "RUNNING"), (terminal_event, terminal_event)]
    executions = traces[-1]["metadata"]["executions"]
    assert [item["mode"] for item in executions] == modes
    assert executions[0]["entry"]["exit_code"] == 1  # real frozen probe exit
    assert all(item["entry"]["evidence"]["package_sha256"] == PACKAGE for item in executions)
    ids = [t["trace_event_id"] for t in traces]
    assert len(ids) == len(set(ids)), "the logical check must survive trace dedup"
    if kind == "not_installed":
        assert device.writes == []
    if kind == "denied":
        assert "init_failure" == result.metadata["termination_reason"] and not device.writes


@pytest.mark.parametrize("code", [124, 125, 143, -15, 137, -9])
def test_platform_interruption_of_probe_fails_init(device_factory, code):
    device = device_factory()
    device.interrupt = code
    result = device.execute()
    assert not result.success
    assert device.reports[0]["family_results"]["power-cycle"]["result"] == "incomplete"
    assert device.modes == ["probe"]


def test_package_unavailable_fails_without_reporting(device_factory):
    device = device_factory()
    device.missing = True
    result = device.execute()
    assert not result.success and "package_unavailable" in result.error_message
    assert device.reports == [] and device.modes == []
    traces = [call.kwargs for call in device.mq.send_step_trace.call_args_list]
    assert [t["event_type"] for t in traces] == ["STARTED", "FAILED"]
    assert traces[-1]["metadata"]["executions"] == []


@pytest.mark.parametrize("kind,missing", [("denied", False), ("clear", True)])
def test_precheck_failure_does_not_start_unestablished_job_teardown(device_factory, kind, missing):
    device = device_factory(kind=kind)
    device.missing = missing
    engine = device.engine()
    result = engine.execute({"lifecycle": {"init": [{"step_id": "setup", "action": "script:powercycle_setup"}],
        "teardown": [{"step_id": "own_disarm", "action": "script:powercycle_disarm", "version": "1.0.8"}]}})
    assert not result.success
    assert device.modes == ([] if missing else ["probe", "disarm"])
    assert result.metadata["teardown_status"] == "SKIPPED"
    assert engine._footprint_families == set()


@pytest.mark.parametrize("kind,success", [("footprint", True), ("denied", False)])
def test_old_control_plane_still_checks_but_does_not_report(device_factory, kind, success):
    device = device_factory(kind=kind, supported=False)
    result = device.execute()
    assert result.success is success
    assert device.modes == ["probe", "disarm"] and device.reports == []


def test_confirmed_422_is_downgraded_and_resent(device_factory):
    device = device_factory()
    device.report_statuses = [422, 200]
    result = device.execute()
    assert result.success, result.error_message
    assert [r["family_results"]["power-cycle"]["result"] for r in device.reports] == ["confirmed", "executed_unverified"]
    final_trace = device.mq.send_step_trace.call_args.kwargs
    assert json.loads(final_trace["output"])["result"] == "executed_unverified"
    assert device.clock == 0, "422 is definitive, not a transient HTTP retry"


def test_init_report_outage_uses_bounded_retry_then_fails(device_factory):
    device = device_factory()
    device.report_statuses = [requests.ConnectionError("test outage") for _ in range(3)]
    result = device.execute()
    assert not result.success and "init_check_unreported" in result.error_message
    assert len(device.reports) == 3 and device.clock == 3
    assert not result.metadata.get("disarm_results")
    traces = [call.kwargs for call in device.mq.send_step_trace.call_args_list]
    assert [t["event_type"] for t in traces] == ["STARTED", "FAILED"]
    assert [e["mode"] for e in traces[-1]["metadata"]["executions"]] == ["probe"]
    assert traces[-1]["output"] is None  # no delivered final result; raw entry stays in metadata
    assert traces[-1]["error_message"] == "init_check_unreported"


def test_initial_lease_loss_prevents_all_device_actions(device_factory):
    device = device_factory()
    device.lease_status = 409
    result = device.execute()
    assert not result.success and device.calls == [] and device.reports == []


def test_lease_lost_after_permit_prevents_probe(device_factory, monkeypatch):
    device = device_factory()
    post = device.post
    checks = []

    def lose_after_first(url, **kwargs):
        if url.endswith("/extend_lock"):
            checks.append(True)
            device.lease_status = 200 if len(checks) == 1 else 409
        return post(url, **kwargs)
    monkeypatch.setattr(requests, "post", lose_after_first)
    result = device.execute()
    assert not result.success and device.calls == [] and device.modes == []
    assert device.reports[0]["family_results"]["power-cycle"]["result"] == "not_executed"


def test_real_runner_terminal_payload_never_contains_init_checks(device_factory, monkeypatch, tmp_path):
    device = device_factory()
    monkeypatch.setattr(pipeline_runner, "get_run_log_dir", lambda _: str(tmp_path / "runner-log"))
    payload = pipeline_runner.execute_pipeline_run({"lifecycle": {"init": [], "teardown": []}},
        1, "u4-test-device", device.engine()._adb, "http://u4-test.invalid", "test-host",
        script_registry=device.registry, local_db=device.local_db, mq_producer=device.mq,
        operation_scheduler=device.scheduler, device_id=1, fencing_token="test-token")
    assert payload["status"] == "FINISHED"
    assert device.reports[0]["family_results"]["power-cycle"]["result"] == "confirmed"
    assert not payload.get("disarm_results")


def test_init_precedes_footprint_and_own_teardown_cannot_be_overwritten(device_factory):
    device = device_factory()
    engine = device.engine()
    original = engine._resolve_action

    def resolve(action, step):
        if action == "script:powercycle_setup":
            def setup(ctx):
                assert device.reports[0]["family_results"]["power-cycle"]["result"] == "confirmed"
                assert device.modes == ["probe"]
                device.kind = "denied"  # own teardown will fail; init confirmation must not mask it
                return StepResult(success=True)
            return setup
        return original(action, step)
    engine._resolve_action = resolve
    result = engine.execute({"lifecycle": {
        "init": [{"step_id": "own_setup", "action": "script:powercycle_setup"}],
        "teardown": [{"step_id": "own_disarm", "action": "script:powercycle_disarm",
                      "version": "1.0.8", "timeout_seconds": 660}],
    }})
    assert result.success
    terminal = result.metadata["disarm_results"]["power-cycle"]
    assert terminal["step_id"] == "own_disarm" and terminal["result"] == "failed"
    assert len(device.reports) == 1 and device.modes == ["probe", "disarm"]


def test_footprinted_job_skipped_own_disarm_is_not_masked_by_init(device_factory):
    """Planner F1: only the init accumulator isolation prevents this leak."""
    device = device_factory()
    engine = device.engine()
    original = engine._resolve_action

    def resolve(action, step):
        if action == "script:powercycle_setup":
            return lambda ctx: StepResult(success=True)
        return original(action, step)
    engine._resolve_action = resolve
    result = engine.execute({"lifecycle": {
        "init": [{"step_id": "own_setup", "action": "script:powercycle_setup"}],
        "teardown": [{"step_id": "own_disarm", "action": "script:powercycle_disarm",
                      "version": "1.0.8", "timeout_seconds": 660, "enabled": False}],
    }})
    assert result.success and engine._footprint_families == {"power-cycle"}
    assert device.reports[0]["family_results"]["power-cycle"]["result"] == "confirmed"
    assert device.modes == ["probe"]
    terminal = result.metadata["disarm_results"]["power-cycle"]
    assert terminal["result"] == "not_executed", terminal
    assert terminal["step_id"] == "own_disarm" and terminal["reason"] == "step_disabled"
    traces = [c.kwargs for c in device.mq.send_step_trace.call_args_list]
    assert any(t["step_id"] == "own_disarm" and t["status"] == "SKIPPED" for t in traces)


def test_checkpoint_resume_skips_precheck_but_keeps_teardown(device_factory):
    """Planner F2 / v1.7.18.8: resume carries the prior successful init."""
    device = device_factory()
    engine = device.engine()
    engine.set_patrol_cycle_resume({"cycle": 5, "failure_streak": 2})
    engine._canceled = True  # finite: exit patrol before its first cycle/sleep
    engine._footprint_families.add("power-cycle")  # footprint from before recovery
    calls = []

    def check():
        calls.append("check")
        return StepResult(success=False, error_message="precheck must not run on resume")

    def teardown(definition, disarm_only=False):
        calls.append("teardown")
        assert definition[0]["step_id"] == "own_disarm"
        return StepResult(success=True, metadata={"teardown_status": "SUCCESS"})
    engine._check_init_returns = check
    engine._execute_teardown_best_effort = teardown
    result = engine.execute({"lifecycle": {
        "init": [{"step_id": "own_setup", "action": "script:powercycle_setup"}],
        "patrol": {"interval_seconds": 1, "steps": [
            {"step_id": "patrol", "action": "script:check_device", "version": "1.0.0"}]},
        "teardown": [{"step_id": "own_disarm", "action": "script:powercycle_disarm", "version": "1.0.8"}],
    }})
    assert calls == ["teardown"], result.metadata
    assert result.metadata["teardown_status"] == "SUCCESS"
    assert device.modes == [] and device.reports == []


def test_aggregate_trace_keeps_both_child_local_logs(device_factory, tmp_path):
    device = device_factory(kind="footprint")
    engine = device.engine()
    engine._log_dir = str(tmp_path / "step-logs")
    result = engine.execute({"lifecycle": {"init": [], "teardown": []}})
    assert result.success and device.modes == ["probe", "disarm"]
    outputs = (tmp_path / "step-logs/init_powercycle_disarm.out.log").read_text().splitlines()
    assert [json.loads(line)["success"] for line in outputs] == [False, True]
    assert "PROGRESS" in (tmp_path / "step-logs/init_powercycle_disarm.err.log").read_text()
