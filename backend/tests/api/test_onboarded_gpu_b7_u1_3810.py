"""#3809 §4 U1: real Plan API and control-plane GPU policy, isolated PG."""
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from backend.agent.contracts.device_return import ONBOARDED
from backend.api.routes.plans import PlanStepIn, _validate_onboarded_wall_clock
from backend.core.job_timeout_config import DISARM_SCRIPT_NAMES
from backend.models.plan import Plan, PlanStep
from backend.models.project import Specialty, TestProject as Project
from backend.models.script import Script
from backend.services import device_return
from backend.services.plan_dispatcher_core import build_lifecycle_from_snapshot, build_plan_snapshot
from backend.services.plan_dispatcher_sync import _fetch_script_metadata


@pytest.fixture
def gpu_plan(db_session):
    db_session.add(Project(project_key="B7_U1", display_name="B7 U1 test"))
    db_session.add(Specialty(key="b7-u1", display_name="B7 U1 test"))
    for name, version in [("gpu_setup", "1.2.3"), ("gpu_disarm", "1.0.0"),
                          ("gpu_disarm", "0.9.0"), ("gpu_finish", "1.1.0")]:
        db_session.add(Script(name=name, version=version, script_type="python",
            nfs_path=f"/test-only/{name}/{version}", content_sha256="a" * 64,
            is_active=True, default_params={"wait_device_online_seconds": 600}, param_schema={}))
    db_session.commit()
    return {"name": "b7-u1-" + uuid4().hex, "project_key": "B7_U1", "specialty_key": "b7-u1", "steps": [
        {"step_key": "setup", "script_name": "gpu_setup", "script_version": "1.2.3",
         "stage": "init", "sort_order": 0, "timeout_seconds": 1200},
        {"step_key": "disarm", "script_name": "gpu_disarm", "script_version": "1.0.0",
         "stage": "teardown", "sort_order": 0, "timeout_seconds": 1200},
        {"step_key": "finish", "script_name": "gpu_finish", "script_version": "1.1.0",
         "stage": "teardown", "sort_order": 1, "timeout_seconds": 900},
    ]}


@pytest.mark.parametrize("entry_point", ["create", "update", "append-chain-tail"])
@pytest.mark.parametrize("case,reason", [
    ("missing", "disarm_missing"), ("disabled", "disarm_disabled"),
    ("old", "disarm_below_min_version"),
])
def test_gpu_plan_save_rejects_invalid_teardown(client, auth_headers, db_session, gpu_plan, entry_point, case, reason):
    valid = deepcopy(gpu_plan)
    url = "/api/v1/plans"
    method = client.post
    payload = deepcopy(valid)
    if entry_point != "create":
        response = client.post(url, headers=auth_headers, json=valid)
        assert response.status_code in (200, 201), response.text
        plan = response.json()["data"]
        url += "/" + str(plan["id"])
        if entry_point == "update":
            method = client.put
            payload = {"steps": payload["steps"], "expected_updated_at": plan["updated_at"]}
        else:
            url += "/append-chain-tail"
            payload["name"] += "-tail"
    if case == "missing":
        payload["steps"] = [s for s in payload["steps"] if s["step_key"] != "disarm"]
    elif case == "disabled":
        payload["steps"][1]["enabled"] = False
    else:
        payload["steps"][1]["script_version"] = "0.9.0"
    before = db_session.query(Plan).count()
    response = method(url, headers=auth_headers, json=payload)
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "ONBOARDED_TEARDOWN_INVALID"
    violation = detail["violations"][0]
    assert violation["family"] == "gpu" and violation["reason"] == reason
    assert violation["required_disarm_script"] == "gpu_disarm"
    assert violation["footprint_step_keys"] == ["setup"]
    assert db_session.query(Plan).count() == before


@pytest.mark.parametrize("timeout,params,minimum", [
    (None, {}, 1200), (1199, {}, 1200),
    (1200, {"wait_device_online_seconds": 900}, 1500),
])
def test_gpu_plan_wall_clock_uses_effective_params(client, auth_headers, gpu_plan, timeout, params, minimum):
    gpu_plan["steps"][1].update(timeout_seconds=timeout, params=params)
    response = client.post("/api/v1/plans", headers=auth_headers, json=gpu_plan)
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "WALL_CLOCK_BELOW_ONBOARDED_BUDGET"
    assert detail["violations"] == [{"step_key": "disarm", "script_name": "gpu_disarm",
        "timeout_seconds": timeout, "min_wall_clock_seconds": minimum}]


def test_valid_gpu_plan_and_shared_timeout_registry(client, auth_headers, gpu_plan):
    response = client.post("/api/v1/plans", headers=auth_headers, json=gpu_plan)
    assert response.status_code in (200, 201), response.text
    assert "gpu_disarm" in DISARM_SCRIPT_NAMES


def test_control_plane_uses_gpu_identity_and_test_combination(monkeypatch):
    combination = {"platform": "TEST", "model": "GPU_TEST", "permission_model": "root",
        "apk_sha256": "a" * 64, "script_name": "gpu_disarm", "script_version": "1.0.0",
        "package_sha256": "b" * 64, "report_ref": "test-only://b7-u1"}
    entry = {"step_id": "disarm", "result": "confirmed", "exit_code": 0, "evidence": {
        **combination, "mode": "disarm", "kind": "ok", "readback": "clear", "success": True,
        "boot_completed": "1"}}
    device = SimpleNamespace(platform="TEST", model="GPU_TEST")
    job = SimpleNamespace(pipeline_def={"lifecycle": {"teardown": [
        {"step_id": "disarm", "action": "script:gpu_disarm", "version": "1.0.0"}]}})
    monkeypatch.setattr(device_return, "manifest_package_sha", lambda *_: "b" * 64)
    # Preserve U1's pre-registration scenario after U3 adds approved classes.
    monkeypatch.setitem(ONBOARDED, "gpu", {**ONBOARDED["gpu"], "verified_device_classes": []})
    assert ONBOARDED["gpu"]["verified_device_classes"] == []
    policy = device_return.evaluate_probe_entry(device, "gpu", entry, token_valid=True, job=job)
    assert policy["result"] == "executed_unverified"
    monkeypatch.setitem(ONBOARDED, "gpu", {**ONBOARDED["gpu"], "verified_device_classes": [combination]})
    policy = device_return.evaluate_probe_entry(device, "gpu", entry, token_valid=True, job=job)
    assert policy["result"] == "confirmed"


@pytest.mark.parametrize("entry_point", ["create", "update", "append-chain-tail"])
@pytest.mark.parametrize("defaults,params,wait,below", [
    ({}, {}, 900, 1200),
    ({"wait_device_online_seconds": 600}, {}, 600, 1199),
    ({"wait_device_online_seconds": 600}, {"wait_device_online_seconds": 300}, 300, 899),
], ids=["schema-default", "script-default-overrides", "step-overrides"])
def test_gpu_schema_defaults_save_and_dispatch_budget(
    client, auth_headers, db_session, gpu_plan, entry_point, defaults, params, wait, below,
):
    """F1: each real save endpoint uses the same effective params as dispatch."""
    script = db_session.query(Script).filter_by(name="gpu_disarm", version="1.0.0").one()
    script.param_schema = {"wait_device_online_seconds": {"type": "integer", "default": 900}}
    script.default_params = defaults
    db_session.commit()
    minimum = wait + 600
    valid = deepcopy(gpu_plan)
    valid["steps"][1].update(timeout_seconds=minimum, params=params)
    payload = deepcopy(valid)
    url = "/api/v1/plans"
    method = client.post
    if entry_point != "create":
        created = client.post(url, headers=auth_headers, json=valid)
        assert created.status_code == 201, created.text
        original = created.json()["data"]
        url += "/" + str(original["id"])
        if entry_point == "update":
            method = client.put
            payload = {"steps": payload["steps"], "expected_updated_at": original["updated_at"]}
        else:
            url += "/append-chain-tail"
            payload["name"] += "-tail"
    payload["steps"][1]["timeout_seconds"] = below
    before = db_session.query(Plan).count()
    rejected = method(url, headers=auth_headers, json=payload)
    assert rejected.status_code == 422, rejected.text
    detail = rejected.json()["detail"]
    assert detail["code"] == "WALL_CLOCK_BELOW_ONBOARDED_BUDGET"
    assert detail["violations"] == [{"step_key": "disarm", "script_name": "gpu_disarm",
        "timeout_seconds": below, "min_wall_clock_seconds": minimum}]
    db_session.rollback()  # discard the failed update's uncommitted timestamps
    assert db_session.query(Plan).count() == before

    payload["steps"][1]["timeout_seconds"] = minimum
    saved = method(url, headers=auth_headers, json=payload)
    assert saved.status_code == (200 if entry_point == "update" else 201), saved.text
    plan_id = saved.json()["data"]["id"]
    plan = db_session.get(Plan, plan_id)
    steps = db_session.query(PlanStep).filter_by(plan_id=plan_id).all()
    snapshot = build_plan_snapshot(plan, steps, _fetch_script_metadata(db_session, steps))
    lifecycle = build_lifecycle_from_snapshot(snapshot)
    disarm = next(s for s in lifecycle["teardown"] if s["step_id"] == "disarm")
    assert disarm["params"]["wait_device_online_seconds"] == wait
    assert disarm["timeout_seconds"] == ONBOARDED["gpu"]["min_wall_clock_seconds"]["gpu_disarm"](disarm["params"])


@pytest.mark.parametrize("entry_point", ["create", "update", "append-chain-tail"])
@pytest.mark.parametrize("schema_default", ["not-an-integer", {"invalid": 900}])
def test_gpu_invalid_schema_default_cannot_bypass_wall_clock(
    client, auth_headers, admin_headers, db_session, gpu_plan, entry_point, schema_default,
):
    """F2: Script API accepts these defaults; Plan must reject an unknown budget."""
    script = db_session.query(Script).filter_by(name="gpu_disarm", version="1.0.0").one()
    script.default_params = {}
    db_session.commit()
    payload = deepcopy(gpu_plan)
    url = "/api/v1/plans"
    method = client.post
    original_id = None
    if entry_point != "create":
        created = client.post(url, headers=auth_headers, json=gpu_plan)
        assert created.status_code == 201, created.text
        original = created.json()["data"]
        original_id = original["id"]
        url += "/" + str(original_id)
        if entry_point == "update":
            method = client.put
            payload = {"steps": payload["steps"], "expected_updated_at": original["updated_at"]}
        else:
            url += "/append-chain-tail"
            payload["name"] += "-tail"

    updated = client.put(f"/api/v1/scripts/{script.id}", headers=admin_headers, json={
        "param_schema": {"wait_device_online_seconds": {
            "type": "integer", "default": schema_default,
        }},
    })
    assert updated.status_code == 200, updated.text
    payload["steps"][1]["timeout_seconds"] = 1
    before_plans = db_session.query(Plan).count()
    before_steps = db_session.query(PlanStep).count()
    rejected = method(url, headers=auth_headers, json=payload)
    assert rejected.status_code == 422, rejected.text
    detail = rejected.json()["detail"]
    assert detail["code"] == "INVALID_STEP_PARAMS"
    assert detail["problems"][0]["step_key"] == "disarm"
    db_session.rollback()
    assert db_session.query(Plan).count() == before_plans
    assert db_session.query(PlanStep).count() == before_steps
    if original_id is not None:
        disarm = db_session.query(PlanStep).filter_by(plan_id=original_id, step_key="disarm").one()
        assert disarm.timeout_seconds == 1200


@pytest.mark.parametrize("entry_point", ["create", "update", "append-chain-tail"])
@pytest.mark.parametrize("defaults,params,minimum", [
    ({"wait_device_online_seconds": 600}, {}, 1200),
    ({}, {"wait_device_online_seconds": 300}, 900),
], ids=["script-default-overrides-invalid-schema", "step-overrides-invalid-schema"])
def test_gpu_invalid_schema_default_can_be_overridden(
    client, auth_headers, admin_headers, db_session, gpu_plan, entry_point, defaults, params, minimum,
):
    """Only effective params matter: valid higher-precedence values remain usable."""
    script = db_session.query(Script).filter_by(name="gpu_disarm", version="1.0.0").one()
    script.default_params = defaults
    db_session.commit()
    payload = deepcopy(gpu_plan)
    payload["steps"][1].update(timeout_seconds=minimum, params=params)
    url = "/api/v1/plans"
    method = client.post
    if entry_point != "create":
        created = client.post(url, headers=auth_headers, json=payload)
        assert created.status_code == 201, created.text
        original = created.json()["data"]
        url += "/" + str(original["id"])
        if entry_point == "update":
            method = client.put
            payload = {"steps": payload["steps"], "expected_updated_at": original["updated_at"]}
        else:
            url += "/append-chain-tail"
            payload["name"] += "-tail"
    updated = client.put(f"/api/v1/scripts/{script.id}", headers=admin_headers, json={
        "param_schema": {"wait_device_online_seconds": {
            "type": "integer", "default": "not-an-integer",
        }},
    })
    assert updated.status_code == 200, updated.text
    saved = method(url, headers=auth_headers, json=payload)
    assert saved.status_code == (200 if entry_point == "update" else 201), saved.text
    plan_id = saved.json()["data"]["id"]
    plan = db_session.get(Plan, plan_id)
    steps = db_session.query(PlanStep).filter_by(plan_id=plan_id).all()
    lifecycle = build_lifecycle_from_snapshot(
        build_plan_snapshot(plan, steps, _fetch_script_metadata(db_session, steps)),
    )
    disarm = next(s for s in lifecycle["teardown"] if s["step_id"] == "disarm")
    assert ONBOARDED["gpu"]["min_wall_clock_seconds"]["gpu_disarm"](disarm["params"]) == minimum
    assert disarm["timeout_seconds"] == minimum


@pytest.mark.parametrize("budget", [None, "not-a-budget", float("inf")])
def test_registered_budget_conversion_failure_rejected(budget):
    """A registered callable's unusable result must not mean unregistered."""
    step = PlanStepIn(step_key="disarm", script_name="gpu_disarm", script_version="1.0.0",
        stage="teardown", sort_order=0, timeout_seconds=1)
    registry = {"gpu": {"min_wall_clock_seconds": {"gpu_disarm": lambda _: budget}}}
    with pytest.raises(HTTPException) as raised:
        _validate_onboarded_wall_clock([step], onboarded=registry)
    assert raised.value.status_code == 422
    assert raised.value.detail["code"] == "INVALID_STEP_PARAMS"
