"""#3809 §4 U1: finite contract checks; GPU combinations are test-only."""
from copy import deepcopy
import json
from unittest.mock import MagicMock

import pytest

from backend.agent import device_return as agent_return
from backend.agent.contracts import device_return as contract
from backend.agent.pipeline_engine import PipelineEngine


GPU_COMBINATION = {
    "platform": "TEST", "model": "GPU_TEST", "permission_model": "root",
    "apk_sha256": "a" * 64, "script_name": "gpu_disarm", "script_version": "1.0.0",
    "package_sha256": "b" * 64, "report_ref": "test-only://b7-u1",
}


def _entry(combination=GPU_COMBINATION, **changes):
    evidence = {**combination, "mode": "disarm", "kind": "ok", "readback": "clear",
                "success": True, "boot_completed": "1"}
    evidence.update(changes)
    return {"exit_code": 0, "evidence": evidence}


def _policy(family, entry, combination, **kwargs):
    return contract.probe_result_policy(
        family, entry, platform=combination["platform"], model=combination["model"],
        script_name=combination["script_name"], script_version=combination["script_version"],
        package_sha256=combination["package_sha256"], token_valid=True, **kwargs,
    )


@pytest.mark.parametrize("index", range(4))
def test_powercycle_four_combinations_keep_identity_and_decisions(index):
    combinations = contract.ONBOARDED["power-cycle"]["verified_device_classes"]
    assert len(combinations) == 4
    combination = combinations[index]
    apks = {
        "MLD_LX2": "f275e1f233e3ac7485469406b3e35a2cf58be0fec0d7e9d60184f180db8a2929",
        "MLD_LX3": "582964aff036cb8785248cd3a149cbf73f13f88bddc51a80c8605b458399e977",
    }
    assert combination["autotesttool_apk_sha256"] == apks[combination["model"]]
    assert combination["permission_model"] in ("root", "non-root")
    assert combination["script_name"] == "powercycle_disarm"
    assert combination["script_version"] == "1.0.8"
    assert combination["package_sha256"] == "98cc16f82fa6c56fe24f37151958f4ad3efd2f5983b1bba36f9340d95cd4a557"
    assert _policy("power-cycle", _entry(combination), combination)["result"] == "confirmed"
    changed = _entry(combination, autotesttool_apk_sha256="0" * 64)
    policy = _policy("power-cycle", changed, combination)
    assert policy["result"] == "executed_unverified"
    assert policy["reason"] == "combination_unverified"


def test_gpu_registration_is_unverified_until_test_combination_is_injected(monkeypatch):
    spec = contract.ONBOARDED["gpu"]
    # Preserve U1's pre-registration scenario after U3 adds approved classes.
    spec = {**spec, "verified_device_classes": []}
    monkeypatch.setitem(contract.ONBOARDED, "gpu", spec)
    assert spec["verified_device_classes"] == []
    assert contract.FAMILY_STEPS["gpu"]["disarm"] == ("gpu_finish",)
    assert contract.FAMILY_STEPS["gpu"]["disarm_onboarded"] == ("gpu_disarm",)
    assert spec["disarm_script"] == "gpu_disarm" and spec["disarm_min_version"] == "1.0.0"
    assert spec["finish_script"] == "gpu_finish" and spec["finish_min_version"] == "1.1.0"
    assert spec["probe_modes"] == ("probe", "disarm")
    result = _policy("gpu", _entry(), GPU_COMBINATION)
    assert result["result"] == "executed_unverified"
    assert result["reason"] == "combination_unverified"
    monkeypatch.setitem(contract.ONBOARDED, "gpu", {**spec, "verified_device_classes": [GPU_COMBINATION]})
    assert _policy("gpu", _entry(), GPU_COMBINATION)["result"] == "confirmed"
    changed = _policy("gpu", _entry(apk_sha256="c" * 64), GPU_COMBINATION)
    assert changed["result"] == "executed_unverified"
    assert changed["reason"] == "combination_unverified"


def test_gpu_tool_identity_does_not_use_powercycle_apk():
    registry = {"gpu": {"verified_device_classes": [GPU_COMBINATION]}}
    policy = _policy("gpu", _entry(autotesttool_apk_sha256="wrong-family"), GPU_COMBINATION,
                     onboarded=registry)
    assert policy["result"] == "confirmed"
    assert policy["observed"]["apk_sha256"] == GPU_COMBINATION["apk_sha256"]
    assert "autotesttool_apk_sha256" not in policy["observed"]


@pytest.mark.parametrize("version,expected", [
    ("1.1.0", "collect"), ("1.0.8", "disarm"), ("1.0.9", "disarm"),
    ("2.0.0", "unknown_legacy_disarm"), ("", "unknown_legacy_disarm"),
    ("invalid", "unknown_legacy_disarm"), ("1.1.1", "unknown_legacy_disarm"),
])
def test_gpu_finish_version_roles(version, expected):
    assert contract.step_teardown_role("gpu_finish", version) == expected


@pytest.mark.parametrize("mode", ["disarm", "probe", None])
@pytest.mark.parametrize("readback", ["future_clear", None])
def test_unknown_readback_fails_closed_even_with_success(mode, readback):
    registry = {"gpu": {"verified_device_classes": [GPU_COMBINATION]}}
    policy = _policy("gpu", _entry(readback=readback, mode=mode), GPU_COMBINATION,
                     onboarded=registry)
    assert policy["result"] == "failed"


@pytest.mark.parametrize("with_evidence", [True, False])
@pytest.mark.parametrize("readback", ["future_clear", None])
def test_engine_unknown_readback_fails_closed(with_evidence, readback):
    engine = PipelineEngine(MagicMock(), "gpu-test", 1, nfs_root="")
    parsed = {"mode": "disarm", "readback": readback, "success": True}
    if with_evidence:
        parsed["evidence"] = _entry()["evidence"]
    engine._record_disarm_for_step(
        {"step_id": "disarm", "action": "script:gpu_disarm", "version": "1.0.0"},
        started=True, success=True, exit_code=0, output=json.dumps(parsed),
    )
    assert engine._disarm_results["gpu"]["result"] == "failed"


def test_gpu_wall_clock_registration_covers_wait_and_bounded_device_work():
    spec = contract.ONBOARDED["gpu"]
    minimum = spec["min_wall_clock_seconds"]["gpu_disarm"]
    assert callable(minimum)
    # The execution allowance covers up to twenty bounded 30-second calls,
    # including identity, stop, setting restoration and all four readbacks.
    assert minimum({}) >= 600 + 20 * 30
    assert minimum({"wait_device_online_seconds": 900}) >= 900 + 20 * 30
    assert spec["disarm_budget_seconds"] == minimum({})


def test_init_checks_run_registered_gpu_and_no_unonboarded_family(monkeypatch, real_return_checks):
    calls = []
    monkeypatch.setattr(agent_return, "execute_registered", lambda _engine, family, mode: (
        calls.append((family, mode)) or {"result": "confirmed", "exit_code": 0}))
    engine = PipelineEngine(MagicMock(), "gpu-test", 1, nfs_root="")
    monkeypatch.setattr(engine, "_report_step_trace_mq", MagicMock())
    result = engine.execute({"lifecycle": {"init": [], "teardown": []}})
    assert result.success
    assert calls == [("power-cycle", "probe"), ("gpu", "probe")]
    assert not result.metadata.get("disarm_results")
    step = agent_return.registered_step("gpu", "probe")
    assert step["action"] == "script:gpu_disarm" and step["version"] == "1.0.0"


def test_active_job_blocks_gpu_debt_using_existing_consumer():
    scheduler = agent_return.OperationScheduler(max_concurrent=1)
    factory = MagicMock()
    consumer = agent_return.ReturnDebtConsumer(
        api_url="http://test.invalid", adb_path="test-only", script_registry=None,
        local_db=MagicMock(), get_scheduler=lambda: scheduler, is_active=lambda _: True,
        engine_factory=factory, launch=lambda fn: fn(),
    )
    consumer.receive({"return_debts": [{"device_id": 1, "serial": "gpu-test",
        "fencing_token": "test-only", "families": ["gpu"]}]},
        [{"serial": "gpu-test", "adb_connected": True}])
    assert consumer._debts[1].families == ["gpu"]
    factory.assert_not_called()
    assert scheduler.held == 0


def test_gpu_collect_result_cannot_overwrite_disarm():
    engine = PipelineEngine(MagicMock(), "gpu-test", 1, nfs_root="")
    prior = {"gpu": {"result": "executed_unverified", "step_id": "disarm", "exit_code": 0}}
    engine._disarm_results = deepcopy(prior)
    engine._record_disarm_for_step(
        {"step_id": "finish", "action": "script:gpu_finish", "version": "1.1.0"},
        started=True, success=False, exit_code=1,
    )
    assert engine._disarm_results == prior
