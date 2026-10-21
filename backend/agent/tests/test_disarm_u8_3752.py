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
