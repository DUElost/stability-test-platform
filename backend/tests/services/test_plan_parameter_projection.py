"""四层投影：来源、五态、掩码与不可变。"""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.models.enums import JobStatus
from backend.models.job import JobInstance
from backend.models.resource_pool import ResourceAllocation, ResourcePool
from backend.services.plan_dispatcher_core import build_lifecycle_from_steps, script_defaults
from backend.services.plan_parameter_projection import (
    project_draft,
    project_job,
    project_saved_plan,
    project_script,
    project_snapshot,
)
from backend.services.script_params import merge_effective_params

SENTINEL = "SENTINEL_PASSWORD"
FIXTURE = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "plan_parameter_merge.json"


def _plan(**overrides):
    fields = dict(
        id=1,
        patrol_interval_seconds=None,
        timeout_seconds=None,
        barrier_timeout_seconds=None,
        barrier_max_wait_seconds=None,
        auto_archive_interval_seconds=None,
        watcher_policy=None,
        suite_id=None,
    )
    fields.update(overrides)
    return SimpleNamespace(**fields)


def _step(**overrides):
    fields = dict(
        step_key="s1",
        script_name="check_device",
        script_version="1.0.0",
        stage="init",
        sort_order=0,
        enabled=True,
        timeout_seconds=None,
        stall_seconds=None,
        retry=0,
        params=None,
    )
    fields.update(overrides)
    return SimpleNamespace(**fields)


def _meta(name="check_device", version="1.0.0", schema=None, defaults=None):
    return {
        (name, version): {
            "param_schema": {} if schema is None else schema,
            "default_params": {} if defaults is None else defaults,
        }
    }


def _item(projection, step_key, *path):
    step = next(item for item in projection.steps if item.step_key == step_key)
    return next(item for item in step.params if tuple(item.path) == path)


def _setting(items, *path):
    return next(item for item in items if tuple(item.path) == path)


def _dump(value) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(value, ensure_ascii=False)


def test_shared_fixture_matches_backend_shallow_merge():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    for case in payload["cases"]:
        merged = merge_effective_params(
            case["param_schema"], case["default_params"], case["step_params"],
        )
        assert merged == case["expected"], case["id"]
    deep = next(case for case in payload["cases"] if case["id"] == "deep-copy")
    defaults = deepcopy(deep["default_params"])
    merged = merge_effective_params(None, defaults, None)
    merged["wifi"]["password"] = "changed"
    assert defaults["wifi"]["password"] == SENTINEL


def test_sources_follow_shallow_priority_and_keep_false_zero_empty():
    schema = {
        "mode": {"type": "string", "default": "schema", "label": "模式", "description": "模式说明"},
        "flag": {"type": "boolean", "default": True},
        "count": {"type": "integer", "default": 5},
    }
    defaults = {"flag": False, "count": 0, "note": ""}
    step = _step(params={"count": 0, "note": None, "free": "x"})
    original = deepcopy(step.params)
    projection = project_saved_plan(
        _plan(), [step], _meta(schema=schema, defaults=defaults), authority="saved",
    )
    step.params["free"] = "mutated"
    defaults["flag"] = True
    assert original["free"] == "x"
    assert _item(projection, "s1", "mode").source == "schema_default"
    assert _item(projection, "s1", "mode").value == "schema"
    assert _item(projection, "s1", "flag").source == "script_default"
    assert _item(projection, "s1", "flag").value is False
    assert _item(projection, "s1", "flag").is_set is True
    assert _item(projection, "s1", "count").source == "step_override"
    assert _item(projection, "s1", "count").value == 0
    assert _item(projection, "s1", "note").value is None
    assert _item(projection, "s1", "note").is_set is False
    assert _item(projection, "s1", "free").source == "step_override"
    assert projection.layer == "L1"
    assert "actual" not in {item.state for step in projection.steps for item in step.params}


def test_nested_object_is_replaced_not_deep_merged_and_masked():
    defaults = {"wifi": {"ssid": "a", "password": SENTINEL}}
    step = _step(script_name="connect_wifi", params={"wifi": {"ssid": "b"}})
    projection = project_saved_plan(
        _plan(),
        [step],
        _meta("connect_wifi", schema={}, defaults=defaults),
        authority="saved",
    )
    assert _item(projection, "s1", "wifi", "ssid").value == "b"
    assert all(tuple(item.path) != ("wifi", "password") for item in projection.steps[0].params)
    assert SENTINEL not in _dump(projection)
    assert defaults["wifi"]["password"] == SENTINEL


def test_missing_script_does_not_invent_defaults():
    projection = project_saved_plan(
        _plan(), [_step(script_name="gone", params={"only": 1})], {}, authority="saved",
    )
    step = projection.steps[0]
    assert step.metadata_missing is True
    assert step.executes is False
    assert "未补造默认值" in (step.missing_reason or "")
    assert _item(projection, "s1", "only").source == "step_override"


def test_explicit_ssid_stays_step_override_and_password_is_pending():
    step = _step(
        script_name="connect_wifi",
        params={"ssid": "kept"},
    )
    projection = project_saved_plan(
        _plan(),
        [step],
        _meta("connect_wifi"),
        authority="saved",
    )
    ssid = _item(projection, "s1", "ssid")
    password = _item(projection, "s1", "password")
    assert ssid.source == "step_override"
    assert ssid.state == "explicit"
    assert ssid.value == "kept"
    assert password.state == "pending_dispatch"
    assert password.value is None
    assert password.is_set is False
    assert any(item.kind == "wifi" and item.state == "pending_dispatch" for item in projection.dispatch_decisions)


def test_plan_settings_states_and_unknown_watcher_key():
    projection = project_saved_plan(
        _plan(
            watcher_policy={"enabled": True, "token": SENTINEL, "extra": {"password": SENTINEL}},
            retry=0,
        ),
        [_step(retry=0, enabled=False, timeout_seconds=0, stall_seconds=None)],
        _meta(),
        authority="saved",
    )
    barrier = _setting(projection.plan_settings, "barrier_max_wait_seconds")
    assert barrier.state == "env_fallback"
    assert barrier.value is None
    assert "1800" in (barrier.fallback_chain or "")
    assert "STP_BARRIER_MAX_WAIT_SECONDS" in (barrier.fallback_chain or "")
    patrol = _setting(projection.plan_settings, "patrol_interval_seconds")
    assert patrol.diagnostic == "无 patrol 时不适用"
    step = projection.steps[0]
    assert _setting(step.settings, "retry").state == "unset_definite"
    assert _setting(step.settings, "retry").value is None
    assert _setting(step.settings, "enabled").state == "explicit"
    assert _setting(step.settings, "enabled").value is False
    timeout = _setting(step.settings, "timeout_seconds")
    assert timeout.state == "explicit"
    assert timeout.value == 0
    stall = _setting(step.settings, "stall_seconds")
    assert stall.state == "env_fallback"
    assert stall.value is None
    token = _setting(projection.watcher_policy.items, "watcher_policy", "token")
    extra = _setting(projection.watcher_policy.items, "watcher_policy", "extra")
    assert token.value is None and token.sensitive is True
    assert extra.value == {"password": None}
    assert SENTINEL not in _dump(projection)
    assert projection.watcher_policy.note == "主机覆盖待派发"


def test_snapshot_ignores_later_edits_and_historical_gap():
    snapshot = {
        "plan": {"id": 7, "name": "frozen", "watcher_policy": {"log_level": "INFO"}},
        "steps": [{
            "step_key": "s1",
            "script_name": "check_device",
            "script_version": "1.0.0",
            "stage": "init",
            "sort_order": 0,
            "enabled": True,
            "retry": 1,
            "params": {"mode": "frozen"},
            "param_schema": {"mode": {"default": "schema", "label": "模式"}},
            "default_params": {"mode": "script"},
        }],
    }
    live_step = _step(params={"mode": "live"})
    projection = project_snapshot(snapshot, {"dispatch_host_watcher_admin_states": {"h1": True}}, plan_id=7, plan_run_id=3)
    live_step.params["mode"] = "later"
    snapshot["steps"][0]["params"]["mode"] = "later"
    assert _item(projection, "s1", "mode").value == "frozen"
    assert _item(projection, "s1", "mode").source == "step_override"
    assert projection.layer == "L2"
    assert projection.watcher_policy.note == "冻结主机决定"
    historical = project_snapshot({"name": "old"}, None, plan_id=7, plan_run_id=3)
    assert "历史缺失" in historical.context.authority
    assert historical.steps == []
    assert any(item.kind == "snapshot" for item in historical.dispatch_decisions)


def test_l3_uses_allocation_not_pool_and_keeps_explicit_ssid():
    snapshot = {
        "plan": {"id": 1, "name": "p", "watcher_policy": {"enabled": True}},
        "steps": [{
            "step_key": "wifi",
            "script_name": "connect_wifi",
            "script_version": "1.0.0",
            "stage": "init",
            "sort_order": 0,
            "enabled": True,
            "retry": 0,
            "params": {"ssid": "kept"},
            "param_schema": {},
            "default_params": {},
        }],
    }
    pipeline = {
        "lifecycle": {
            "init": [{
                "step_id": "wifi",
                "action": "script:connect_wifi",
                "version": "1.0.0",
                "params": {"ssid": "kept", "password": SENTINEL},
                "retry": 0,
            }],
            "teardown": [],
        }
    }
    pool_secret = "SENTINEL_POOL_PASSWORD"
    projection = project_job(
        plan_snapshot=snapshot,
        run_context={},
        job_id=11,
        device_id=2,
        host_id="h1",
        plan_run_id=4,
        plan_id=1,
        pipeline_def=pipeline,
        allocation_params={"ssid": "pool-a", "password": SENTINEL, "pool_name": "a"},
        claim_watcher_policy={"enabled": True},
    )
    assert _item(projection, "wifi", "ssid").source == "step_override"
    assert _item(projection, "wifi", "ssid").value == "kept"
    password = _item(projection, "wifi", "password")
    assert password.source == "dispatch_injection"
    assert password.value is None
    assert password.is_set is True
    assert password.state == "explicit"
    assert pool_secret not in _dump(projection)
    assert SENTINEL not in _dump(projection)
    open_snapshot = deepcopy(snapshot)
    open_snapshot["steps"][0]["params"] = {}
    other = project_job(
        plan_snapshot=open_snapshot,
        run_context={},
        job_id=12,
        device_id=3,
        host_id="h1",
        plan_run_id=4,
        plan_id=1,
        pipeline_def={
            "lifecycle": {
                "init": [{
                    "step_id": "wifi",
                    "action": "script:connect_wifi",
                    "version": "1.0.0",
                    "params": {"ssid": "pool-b", "password": "SENTINEL_OTHER"},
                    "retry": 0,
                }],
                "teardown": [],
            }
        },
        allocation_params={"ssid": "pool-b", "password": "SENTINEL_OTHER"},
        claim_watcher_policy={"enabled": True},
    )
    assert _item(projection, "wifi", "ssid").value == "kept"
    assert _item(other, "wifi", "ssid").value == "pool-b"
    assert _item(other, "wifi", "ssid").source == "dispatch_injection"
    assert "SENTINEL_OTHER" not in _dump(other)


def test_unmaterialized_job_does_not_emit_actual():
    projection = project_job(
        plan_snapshot={"plan": {"id": 1, "name": "p"}, "steps": []},
        run_context={},
        job_id=1,
        device_id=1,
        host_id="h",
        plan_run_id=1,
        plan_id=1,
        pipeline_def={"lifecycle": {"init": [], "teardown": []}},
        allocation_params=None,
        claim_watcher_policy=None,
    )
    assert projection.layer == "L3"
    dumped = _dump(projection)
    assert '"actual"' not in dumped
    assert any(item.state == "pending_dispatch" for item in projection.dispatch_decisions)


def test_inactive_host_forces_only_that_policy():
    snapshot = {
        "plan": {"id": 1, "watcher_policy": {"log_level": "INFO", "enabled": True}},
        "steps": [],
    }
    run_context = {"dispatch_host_watcher_admin_states": {"h-off": False, "h-on": True}}
    off = project_job(
        plan_snapshot=snapshot,
        run_context=run_context,
        job_id=1,
        device_id=1,
        host_id="h-off",
        plan_run_id=1,
        plan_id=1,
        pipeline_def={"lifecycle": {"init": [], "teardown": []}},
        allocation_params=None,
        claim_watcher_policy={"log_level": "INFO", "enabled": False},
    )
    on = project_job(
        plan_snapshot=snapshot,
        run_context=run_context,
        job_id=2,
        device_id=2,
        host_id="h-on",
        plan_run_id=1,
        plan_id=1,
        pipeline_def={"lifecycle": {"init": [], "teardown": []}},
        allocation_params=None,
        claim_watcher_policy={"log_level": "INFO", "enabled": True},
    )
    assert _setting(off.watcher_policy.items, "watcher_policy", "enabled").value is False
    assert off.watcher_policy.effective_policy == {"log_level": "INFO", "enabled": False}
    assert _setting(on.watcher_policy.items, "watcher_policy", "enabled").value is True
    assert "按现有 claim 口径推导的下发策略" in off.watcher_policy.note


def test_script_projection_masks_defaults_but_merge_ignores_docs(tmp_path, monkeypatch):
    schema = {"password": {"type": "string", "default": SENTINEL, "label": "密码"}}
    defaults = {"password": SENTINEL}
    before = merge_effective_params(schema, defaults, None)
    lifecycle_plan = _plan()
    lifecycle_step = _step(params=None)
    before_life = build_lifecycle_from_steps(
        lifecycle_plan, [lifecycle_step], script_defaults(_meta(schema=schema, defaults=defaults)),
    )
    monkeypatch.setattr("backend.services.param_docs._DOCS_DIR", tmp_path)
    (tmp_path / "check_device.json").write_text(json.dumps({
        "schema_version": 1,
        "script_name": "check_device",
        "entries": [{
            "path": ["password"],
            "label": "密码",
            "meaning": "连接口令",
            "sensitive": True,
        }],
    }), encoding="utf-8")
    after = merge_effective_params(schema, defaults, None)
    after_life = build_lifecycle_from_steps(
        lifecycle_plan, [lifecycle_step], script_defaults(_meta(schema=schema, defaults=defaults)),
    )
    assert before == after
    assert before_life == after_life
    projected = project_script(
        script_name="check_device",
        script_version="1.0.0",
        param_schema=schema,
        default_params=defaults,
    )
    assert projected.params[0].value is None
    assert projected.params[0].sensitive is True
    assert SENTINEL not in _dump(projected)
    assert defaults["password"] == SENTINEL


def test_draft_does_not_require_suite_binding():
    projection = project_draft(
        {},
        [_step(script_name="mtbf_case", params={"project": "declared"})],
        _meta("mtbf_case"),
    )
    assert projection.context.plan_id is None
    assert _item(projection, "s1", "project").value == "declared"
    assert projection.context.authority.startswith("未保存草稿")


@pytest.mark.asyncio
async def test_l3_loader_matches_enrich_and_ignores_live_host(
    db_session, sample_host, sample_device, sample_plan, sample_plan_run,
):
    from backend.core.database import AsyncSessionLocal
    from backend.models.job import JobInstance as JobModel
    from backend.services.agent_claim import enrich_job_metadata
    from backend.services.plan_parameter_projection import load_job_parameter_projection

    sample_host.watcher_admin_active = True
    sample_plan_run.plan_snapshot = {
        "plan": {
            "id": sample_plan.id,
            "watcher_policy": {"log_level": "INFO", "enabled": True},
        },
        "steps": [],
    }
    sample_plan_run.run_context = {
        "dispatch_host_watcher_admin_states": {sample_host.id: False},
    }
    pool = ResourcePool(
        name="pool-a",
        resource_type="wifi",
        config={"ssid": "live", "password": "SENTINEL_LIVE_POOL"},
    )
    db_session.add(pool)
    db_session.flush()
    job = JobInstance(
        plan_run_id=sample_plan_run.id,
        plan_id=sample_plan.id,
        device_id=sample_device.id,
        host_id=sample_host.id,
        status=JobStatus.PENDING.value,
        pipeline_def={"lifecycle": {"init": [], "teardown": []}},
    )
    db_session.add(job)
    db_session.flush()
    db_session.add(ResourceAllocation(
        job_instance_id=job.id,
        resource_pool_id=pool.id,
        device_id=sample_device.id,
        allocated_params={"ssid": "frozen-ssid", "password": SENTINEL},
    ))
    db_session.commit()
    pool.config = {"ssid": "changed", "password": "SENTINEL_LIVE_POOL"}
    db_session.commit()

    async with AsyncSessionLocal() as db:
        loaded_job = await db.get(JobModel, job.id)
        _serials, policies = await enrich_job_metadata(db, [loaded_job])
        projection = await load_job_parameter_projection(db, sample_plan_run.id, job.id)
    assert projection.watcher_policy.effective_policy == policies[job.id]
    assert policies[job.id]["enabled"] is False
    assert "SENTINEL_LIVE_POOL" not in _dump(projection)
    assert SENTINEL not in _dump(projection)
