"""四层参数投影。

值只来自 ``merge_effective_params`` 与现有 lifecycle builder。来源标注不是第二套
执行参数。掩码在序列化前完成，调用方传入的对象不会被修改。
"""
from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable, Mapping

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.redaction import sensitive_query_keys
from backend.models.job import JobInstance
from backend.models.plan_run import PlanRun
from backend.models.resource_pool import ResourceAllocation
from backend.services.agent_claim import enrich_job_metadata
from backend.api.schemas.plan_parameter_projection import (
    DispatchDecision,
    ParameterItem,
    ParameterProjection,
    ProjectionContext,
    ProjectionStep,
    SafeDebug,
    SafeDebugStep,
    ScriptParameterProjection,
    WatcherProjection,
)
from backend.services.param_docs import (
    load_param_docs,
    resolve_doc,
    schema_field_for_path,
    sensitive_registry_paths,
)
from backend.services.parameter_redaction import (
    contains_text,
    is_set_value,
    redact_params,
    redact_schema,
    union_sensitive_paths,
)
from backend.services.plan_dispatcher_core import (
    PlanDispatchError,
    build_lifecycle_from_snapshot,
    build_lifecycle_from_steps,
    extract_dispatch_host_watcher_admin_states,
    inject_suite_params,
    inject_wifi_params,
    iter_lifecycle_steps,
    script_defaults,
)
from backend.services.script_params import merge_effective_params

_SETTINGS_PATH = Path(__file__).resolve().parents[1] / "schemas" / "plan_settings.json"
_PROBE = "\u0000stp-u1-probe"
_MISSING = object()
_PLAN_NUMBER_KEYS = (
    "patrol_interval_seconds",
    "timeout_seconds",
    "barrier_timeout_seconds",
    "barrier_max_wait_seconds",
    "auto_archive_interval_seconds",
)
# step_params_for_dispatch 的产出键。投影不得调用该函数（它会读活套件表）。
_SUITE_PARAM_KEYS = ("expected_testpoint_count", "project")
_CLAIM_NOTE = "按现有 claim 口径推导的下发策略"


class ProjectionLookupError(LookupError):
    def __init__(self, code: str, message: str, status: int = 404) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def load_plan_settings_document() -> dict[str, Any]:
    return json.loads(_SETTINGS_PATH.read_text(encoding="utf-8"))


def validate_plan_settings_document(data: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        return ["schema_version must be 1"]
    entries = data.get("entries")
    if not isinstance(entries, list) or not entries:
        return ["entries must be a non-empty list"]
    required = (
        "scope", "path", "label", "meaning", "unit", "unset_state",
        "fallback_chain", "write_boundary", "ui_editable", "semantic_anchors",
    )
    seen: set[tuple] = set()
    for index, entry in enumerate(entries):
        prefix = f"entries[{index}]"
        if not isinstance(entry, dict):
            errors.append(f"{prefix} must be an object")
            continue
        for key in required:
            if key not in entry:
                errors.append(f"{prefix} missing {key}")
        if entry.get("scope") not in {"plan", "step", "watcher"}:
            errors.append(f"{prefix}.scope invalid")
        path = entry.get("path")
        if not isinstance(path, list) or not path:
            errors.append(f"{prefix}.path invalid")
        elif (entry.get("scope"), tuple(path)) in seen:
            errors.append(f"{prefix}.path duplicate")
        else:
            seen.add((entry.get("scope"), tuple(path)))
        if entry.get("unset_state") not in {"unset_definite", "env_fallback"}:
            errors.append(f"{prefix}.unset_state invalid")
        if not isinstance(entry.get("ui_editable"), bool):
            errors.append(f"{prefix}.ui_editable invalid")
        anchors = entry.get("semantic_anchors")
        if not isinstance(anchors, list) or not anchors or not all(
            isinstance(item, str) and item for item in anchors
        ):
            errors.append(f"{prefix}.semantic_anchors invalid")
    return errors


def _settings_entries() -> list[dict[str, Any]]:
    document = load_plan_settings_document()
    errors = validate_plan_settings_document(document)
    if errors:
        raise RuntimeError("plan_settings.json invalid: " + "; ".join(errors))
    return document["entries"]


def _entries(scope: str) -> list[dict[str, Any]]:
    return [entry for entry in _settings_entries() if entry["scope"] == scope]


def project_saved_plan(
    plan: Any,
    steps: list[Any],
    metadata: Mapping[tuple[str, str], Mapping[str, Any]],
    *,
    authority: str,
) -> ParameterProjection:
    return _project_l1(plan, list(steps), metadata, authority=authority)


def project_draft(
    plan_fields: Mapping[str, Any],
    steps: list[Any],
    metadata: Mapping[tuple[str, str], Mapping[str, Any]],
) -> ParameterProjection:
    plan = SimpleNamespace(
        id=None,
        patrol_interval_seconds=plan_fields.get("patrol_interval_seconds"),
        timeout_seconds=plan_fields.get("timeout_seconds"),
        barrier_timeout_seconds=plan_fields.get("barrier_timeout_seconds"),
        barrier_max_wait_seconds=plan_fields.get("barrier_max_wait_seconds"),
        auto_archive_interval_seconds=plan_fields.get("auto_archive_interval_seconds"),
        watcher_policy=deepcopy(plan_fields.get("watcher_policy")),
        suite_id=None,
    )
    namespaces = []
    for step in steps:
        namespaces.append(SimpleNamespace(
            step_key=_get(step, "step_key"),
            script_name=_get(step, "script_name"),
            script_version=_get(step, "script_version"),
            stage=_get(step, "stage"),
            sort_order=_get(step, "sort_order") or 0,
            enabled=True if _get(step, "enabled") is None else bool(_get(step, "enabled")),
            timeout_seconds=_get(step, "timeout_seconds"),
            stall_seconds=_get(step, "stall_seconds"),
            retry=0 if _get(step, "retry") is None else _get(step, "retry"),
            params=deepcopy(_get(step, "params")) if isinstance(_get(step, "params"), dict) else _get(step, "params"),
        ))
    return _project_l1(
        plan,
        namespaces,
        metadata,
        authority="未保存草稿；只读计算，未写数据库",
    )


def project_snapshot(
    plan_snapshot: Any,
    run_context: Any,
    *,
    plan_id: int | None,
    plan_run_id: int | None,
    read_at: datetime | None = None,
) -> ParameterProjection:
    return _project_l2(
        plan_snapshot,
        run_context,
        plan_id=plan_id,
        plan_run_id=plan_run_id,
        read_at=read_at,
    )


def project_script(
    *,
    script_name: str,
    script_version: str,
    param_schema: Any,
    default_params: Any,
) -> ScriptParameterProjection:
    schema = deepcopy(param_schema) if isinstance(param_schema, dict) else {}
    defaults = deepcopy(default_params) if isinstance(default_params, dict) else {}
    merged = merge_effective_params(schema, defaults, None)
    sources = _source_map(schema, defaults, None)
    paths = _sensitive_paths(script_name, script_version)
    params = _leaf_items(
        script_name,
        script_version,
        schema,
        merged,
        sources,
        paths,
        pending=set(),
        factors={},
        authority="脚本默认参数与 schema",
    )
    debug = SafeDebug(steps=[
        SafeDebugStep(
            script_name=script_name,
            script_version=script_version,
            params=redact_params(merged, paths),
            param_schema=redact_schema(schema, paths),
            default_params=redact_params(defaults, paths),
            step_params=None,
        )
    ])
    _reject_probe(debug)
    return ScriptParameterProjection(
        script_name=script_name,
        script_version=script_version,
        params=params,
        safe_debug=debug,
    )


def project_job(
    *,
    plan_snapshot: Any,
    run_context: Any,
    job_id: int,
    device_id: int | None,
    host_id: str | None,
    plan_run_id: int | None,
    plan_id: int | None,
    pipeline_def: Any,
    allocation_params: Mapping[str, Any] | None,
    claim_watcher_policy: Mapping[str, Any] | None,
) -> ParameterProjection:
    return _project_l3(
        plan_snapshot=plan_snapshot,
        run_context=run_context,
        job_id=job_id,
        device_id=device_id,
        host_id=host_id,
        plan_run_id=plan_run_id,
        plan_id=plan_id,
        pipeline_def=pipeline_def,
        allocation_params=deepcopy(dict(allocation_params)) if isinstance(allocation_params, Mapping) else None,
        claim_watcher_policy=deepcopy(dict(claim_watcher_policy)) if isinstance(claim_watcher_policy, Mapping) else None,
    )


async def load_job_parameter_projection(
    db: AsyncSession, run_id: int, job_id: int,
) -> ParameterProjection:
    job = await db.get(JobInstance, job_id)
    if job is None or job.plan_run_id != run_id:
        raise ProjectionLookupError("JOB_NOT_FOUND", "job 不属于该 plan run")
    plan_run = await db.get(PlanRun, run_id)
    if plan_run is None:
        raise ProjectionLookupError("PLAN_RUN_NOT_FOUND", "plan run 不存在")
    rows = await db.execute(
        select(ResourceAllocation).where(ResourceAllocation.job_instance_id == job.id)
    )
    allocation = rows.scalars().first()
    _serials, policies = await enrich_job_metadata(db, [job])
    return project_job(
        plan_snapshot=plan_run.plan_snapshot,
        run_context=plan_run.run_context,
        job_id=job.id,
        device_id=job.device_id,
        host_id=job.host_id,
        plan_run_id=plan_run.id,
        plan_id=plan_run.plan_id,
        pipeline_def=job.pipeline_def,
        allocation_params=None if allocation is None else allocation.allocated_params,
        claim_watcher_policy=policies.get(job.id),
    )


def _project_l1(plan: Any, steps: list[Any], metadata: Mapping, *, authority: str) -> ParameterProjection:
    norm = [_norm_step(step) for step in steps]
    meta = {
        key: {
            "param_schema": deepcopy(value.get("param_schema") or {}),
            "default_params": deepcopy(value.get("default_params") or {}),
        }
        for key, value in metadata.items()
    }
    lifecycle_params = _l1_lifecycle_params(plan, steps, meta)
    lifecycle = _lifecycle_or_none(plan, steps, meta)
    wifi_paths, suite_paths = _l1_pending(plan, lifecycle)
    built_steps = []
    debug_steps = []
    for step in norm:
        key = (step["script_name"], step["script_version"])
        info = meta.get(key)
        missing = info is None
        schema = {} if info is None else info["param_schema"]
        defaults = {} if info is None else info["default_params"]
        step_params = step["params"] if isinstance(step["params"], dict) else None
        if missing:
            merged = deepcopy(step_params or {})
            sources = {name: "step_override" for name in merged}
        else:
            sources = _source_map(schema, defaults, step_params)
            from_builder = None if lifecycle_params is None else lifecycle_params.get(step["step_key"])
            merged = deepcopy(from_builder) if isinstance(from_builder, dict) else merge_effective_params(
                schema, defaults, step_params,
            )
        paths = _sensitive_paths(step["script_name"], step["script_version"])
        pending = set()
        factors: dict[tuple, list[str]] = {}
        if step["enabled"] and not missing:
            for path in wifi_paths.get(step["step_key"], ()):
                pending.add(path)
                factors.setdefault(path, []).append("由所选 WiFi 池或自动选池在物化时按设备分配")
            for path in suite_paths.get(step["step_key"], ()):
                pending.add(path)
                factors.setdefault(path, []).append("由绑定套件在派发时冻结")
        built_steps.append(ProjectionStep(
            step_key=step["step_key"],
            script_name=step["script_name"],
            script_version=step["script_version"],
            stage=step["stage"],
            sort_order=step["sort_order"],
            enabled=step["enabled"],
            executes=step["enabled"] and not missing,
            metadata_missing=missing,
            missing_reason="引用的脚本不存在或已停用，未补造默认值" if missing else None,
            params=[] if missing and not merged else _ensure_pending(
                _leaf_items(
                    step["script_name"], step["script_version"], schema, merged, sources, paths,
                    pending=pending, factors=factors, authority=authority,
                ),
                pending, factors, step["script_name"], step["script_version"], schema, paths,
                authority,
            ),
            settings=_step_settings(step),
        ))
        debug_steps.append(SafeDebugStep(
            step_key=step["step_key"],
            script_name=step["script_name"],
            script_version=step["script_version"],
            stage=step["stage"],
            enabled=step["enabled"],
            params=redact_params(merged, paths),
            param_schema=redact_schema(schema, paths),
            default_params=redact_params(defaults, paths),
            step_params=redact_params(step_params, paths) if step_params is not None else None,
        ))
    plan_fields = _plan_fields(plan)
    projection = _assemble(
        layer="L1",
        authority=authority,
        plan_id=getattr(plan, "id", None),
        steps=built_steps,
        plan_fields=plan_fields,
        norm_steps=norm,
        run_context=None,
        claim_policy=None,
        debug_steps=debug_steps,
        decisions=_l1_decisions(wifi_paths, suite_paths),
    )
    _reject_probe(projection)
    return projection


def _project_l2(
    plan_snapshot: Any,
    run_context: Any,
    *,
    plan_id: int | None,
    plan_run_id: int | None,
    read_at: datetime | None = None,
) -> ParameterProjection:
    snapshot = plan_snapshot if isinstance(plan_snapshot, dict) else {}
    historical = not isinstance(plan_snapshot, dict) or (
        "plan" not in snapshot and "steps" not in snapshot
    )
    raw_steps = snapshot.get("steps") if isinstance(snapshot.get("steps"), list) else []
    plan_data = snapshot.get("plan") if isinstance(snapshot.get("plan"), dict) else {}
    lifecycle = _snapshot_lifecycle(snapshot)
    wifi_paths, suite_paths = _l2_pending(lifecycle, run_context)
    built_steps = []
    debug_steps = []
    for raw in raw_steps:
        if not isinstance(raw, dict):
            continue
        step = _norm_snapshot_step(raw)
        schema_present = "param_schema" in raw
        defaults_present = "default_params" in raw
        params_present = "params" in raw
        missing = not schema_present and not defaults_present
        schema = deepcopy(raw.get("param_schema") or {}) if schema_present else {}
        defaults = deepcopy(raw.get("default_params") or {}) if defaults_present else {}
        step_params = deepcopy(raw.get("params")) if isinstance(raw.get("params"), dict) else None
        if missing:
            merged = deepcopy(step_params or {})
            sources = {name: "step_override" for name in merged}
        else:
            sources = _source_map(schema, defaults, step_params if params_present else None)
            from_builder = None if lifecycle is None else _lifecycle_param_map(lifecycle).get(step["step_key"])
            merged = deepcopy(from_builder) if isinstance(from_builder, dict) else merge_effective_params(
                schema, defaults, step_params,
            )
        paths = _sensitive_paths(step["script_name"], step["script_version"])
        pending: set[tuple] = set()
        factors: dict[tuple, list[str]] = {}
        if step["enabled"] and not missing:
            for path in wifi_paths.get(step["step_key"], ()):
                pending.add(path)
                factors.setdefault(path, []).append("WiFi 按设备分配属于下发层，本层只保留冻结设计")
            for path in suite_paths.get(step["step_key"], ()):
                pending.add(path)
                factors.setdefault(path, []).append("套件填空属于下发层，本层不写入具体数量")
        reason = None
        if missing:
            reason = "历史缺失：快照没有 param_schema/default_params，未回读当前脚本"
        built_steps.append(ProjectionStep(
            step_key=step["step_key"],
            script_name=step["script_name"],
            script_version=step["script_version"],
            stage=step["stage"],
            sort_order=step["sort_order"],
            enabled=step["enabled"],
            executes=step["enabled"],
            metadata_missing=missing,
            missing_reason=reason,
            params=_ensure_pending(
                _leaf_items(
                    step["script_name"], step["script_version"], schema, merged, sources, paths,
                    pending=pending, factors=factors,
                    authority="plan_snapshot",
                ),
                pending, factors, step["script_name"], step["script_version"], schema, paths,
                "plan_snapshot",
            ) if (merged or not missing or pending) else [],
            settings=_step_settings(step),
        ))
        debug_steps.append(SafeDebugStep(
            step_key=step["step_key"],
            script_name=step["script_name"],
            script_version=step["script_version"],
            stage=step["stage"],
            enabled=step["enabled"],
            params=redact_params(merged, paths),
            param_schema=redact_schema(schema, paths),
            default_params=redact_params(defaults, paths),
            step_params=redact_params(step_params, paths) if step_params is not None else None,
        ))
    authority = "plan_snapshot 与 run_context 冻结决定；未回读当前 PlanStep 或 Script"
    if historical:
        authority += "；历史缺失"
    decisions = _frozen_host_decisions(run_context)
    if historical:
        decisions.append(DispatchDecision(
            kind="snapshot",
            state="unset_definite",
            factors=["历史缺失：无快照，未回读当前 Plan 或 Script"],
        ))
    plan_fields = {
        "id": plan_data.get("id", plan_id),
        "watcher_policy": deepcopy(plan_data.get("watcher_policy")),
        "suite_id": None,
        **{key: plan_data.get(key) for key in _PLAN_NUMBER_KEYS},
    }
    projection = _assemble(
        layer="L2",
        authority=authority,
        plan_id=plan_id,
        plan_run_id=plan_run_id,
        steps=built_steps,
        plan_fields=plan_fields,
        norm_steps=[_norm_snapshot_step(raw) for raw in raw_steps if isinstance(raw, dict)],
        run_context=run_context,
        claim_policy=None,
        debug_steps=debug_steps,
        decisions=decisions,
        read_at=read_at,
    )
    _reject_probe(projection)
    return projection


def _project_l3(
    *,
    plan_snapshot: Any,
    run_context: Any,
    job_id: int,
    device_id: int | None,
    host_id: str | None,
    plan_run_id: int | None,
    plan_id: int | None,
    pipeline_def: Any,
    allocation_params: dict | None,
    claim_watcher_policy: dict | None,
) -> ParameterProjection:
    baseline = _project_l2(
        plan_snapshot, run_context, plan_id=plan_id, plan_run_id=plan_run_id,
    )
    job_pipeline = pipeline_def if isinstance(pipeline_def, dict) else {}
    job_steps = {
        step.get("step_id"): step
        for _phase, step in iter_lifecycle_steps(job_pipeline)
        if isinstance(step, dict)
    }
    materialized = bool(job_steps)
    snapshot = plan_snapshot if isinstance(plan_snapshot, dict) else {}
    l2_lifecycle = _snapshot_lifecycle(snapshot)
    baseline_params = (
        _lifecycle_param_map(l2_lifecycle) if isinstance(l2_lifecycle, dict) else {}
    )
    wifi = None
    if isinstance(allocation_params, dict) and allocation_params.get("ssid"):
        wifi = {
            "ssid": allocation_params.get("ssid"),
            "password": allocation_params.get("password", ""),
        }
    suite = (
        _suite_params_from_frozen(run_context, l2_lifecycle, job_pipeline)
        if materialized else None
    )
    built_steps: list[ProjectionStep] = []
    debug_steps: list[SafeDebugStep] = []
    decisions = list(baseline.dispatch_decisions)
    if not materialized:
        decisions.append(DispatchDecision(
            kind="materialization",
            state="pending_dispatch",
            job_id=job_id,
            device_id=device_id,
            host_id=host_id,
            factors=["尚未物化，不伪造下发值"],
        ))
        for step in baseline.steps:
            built_steps.append(step.model_copy(update={
                "params": [
                    item.model_copy(update={
                        "state": "pending_dispatch" if item.state != "explicit" else item.state,
                        "value": None if item.state != "explicit" else item.value,
                    })
                    for item in step.params
                ],
            }))
        debug_steps = list(baseline.safe_debug.steps)
    else:
        base_by_key = {step.step_key: step for step in baseline.steps}
        for step_id, job_step in job_steps.items():
            base = base_by_key.get(step_id)
            action = str(job_step.get("action") or "")
            script_name = action.split("script:", 1)[1] if action.startswith("script:") else (
                base.script_name if base else None
            )
            version = job_step.get("version") or (base.script_version if base else None)
            paths = _sensitive_paths(script_name, version)
            base_params = deepcopy(baseline_params.get(step_id) or {})
            inj_params = base_params
            wifi_changed: set[tuple] = set()
            suite_changed: set[tuple] = set()
            if isinstance(l2_lifecycle, dict) and (wifi or suite):
                if wifi:
                    wifi_changed = _changed_by_step(l2_lifecycle, wifi, None).get(step_id, set())
                both_changed = _changed_by_step(l2_lifecycle, wifi, suite).get(step_id, set())
                suite_changed = both_changed - wifi_changed
                _before, injected = _apply_inject(l2_lifecycle, wifi, suite)
                inj_params = _params_of(injected, step_id)
            job_params = job_step.get("params") or {}
            sources = {}
            if base is not None:
                for item in base.params:
                    if item.path and item.source:
                        sources.setdefault(item.path[0], item.source)
            params = _l3_items(
                script_name, version, base_params, inj_params, job_params, sources, paths,
                wifi_changed, suite_changed,
            )
            built_steps.append(ProjectionStep(
                step_key=step_id,
                script_name=script_name,
                script_version=version,
                stage=base.stage if base else None,
                sort_order=base.sort_order if base else 0,
                enabled=True if base is None else base.enabled,
                executes=True,
                metadata_missing=bool(base and base.metadata_missing),
                missing_reason=(
                    "来源不可追溯"
                    if base is None or base.metadata_missing
                    else None
                ),
                params=params,
                settings=list(base.settings) if base else [],
            ))
            debug_steps.append(SafeDebugStep(
                step_key=step_id,
                script_name=script_name,
                script_version=version,
                enabled=True if base is None else base.enabled,
                stage=base.stage if base else None,
                params=redact_params(job_params, paths),
                param_schema={},
                default_params={},
                step_params=None,
            ))
        if wifi:
            decisions.append(DispatchDecision(
                kind="wifi",
                state="explicit",
                job_id=job_id,
                device_id=device_id,
                host_id=host_id,
                factors=["WiFi 取该 job 的 ResourceAllocation，不读当前池配置"],
            ))
    plan_fields = _plan_fields_from_snapshot(plan_snapshot, plan_id)
    projection = _assemble(
        layer="L3",
        authority=_CLAIM_NOTE + "；值来自 Job.pipeline_def 与 ResourceAllocation",
        plan_id=plan_id,
        plan_run_id=plan_run_id,
        job_id=job_id,
        device_id=device_id,
        host_id=host_id,
        steps=built_steps,
        plan_fields=plan_fields,
        norm_steps=[],
        run_context=run_context,
        claim_policy=claim_watcher_policy,
        debug_steps=debug_steps,
        decisions=decisions,
        has_patrol=_snapshot_has_patrol(plan_snapshot),
    )
    _reject_probe(projection)
    return projection


def _assemble(
    *,
    layer: str,
    authority: str,
    steps: list[ProjectionStep],
    plan_fields: Mapping[str, Any],
    norm_steps: list[dict],
    run_context: Any,
    claim_policy: Mapping[str, Any] | None,
    debug_steps: list[SafeDebugStep],
    decisions: list[DispatchDecision],
    plan_id: int | None = None,
    plan_run_id: int | None = None,
    job_id: int | None = None,
    device_id: int | None = None,
    host_id: str | None = None,
    has_patrol: bool | None = None,
    read_at: datetime | None = None,
) -> ParameterProjection:
    patrol = _has_enabled_patrol(norm_steps) if has_patrol is None else has_patrol
    policy = plan_fields.get("watcher_policy")
    if not isinstance(policy, dict):
        policy = {} if policy is None else {}
    watcher = _watcher_block(policy, layer, run_context, claim_policy)
    safe_plan = {
        key: plan_fields.get(key) for key in _PLAN_NUMBER_KEYS
    }
    projection = ParameterProjection(
        layer=layer,  # type: ignore[arg-type]
        context=ProjectionContext(
            plan_id=plan_id if plan_id is not None else plan_fields.get("id"),
            plan_run_id=plan_run_id,
            job_id=job_id,
            device_id=device_id,
            host_id=host_id,
            read_at=read_at or datetime.now(timezone.utc),
            authority=authority,
        ),
        steps=steps,
        plan_settings=_plan_setting_items(plan_fields, patrol),
        watcher_policy=watcher,
        dispatch_decisions=decisions,
        safe_debug=SafeDebug(
            steps=debug_steps,
            plan_settings=redact_params(safe_plan, ()),
            watcher_policy=redact_params(policy, ()),
        ),
    )
    return projection


def _plan_setting_items(plan_fields: Mapping[str, Any], patrol: bool) -> list[ParameterItem]:
    items = []
    for entry in _entries("plan"):
        key = entry["path"][-1]
        raw = plan_fields.get(key)
        applicable = True
        diagnostic = None
        if key in {"patrol_interval_seconds", "timeout_seconds"} and raw is None and not patrol:
            applicable = False
            diagnostic = "无 patrol 时不适用"
        if key == "watcher_policy":
            items.append(_policy_parent_item(entry, raw if isinstance(raw, dict) else plan_fields.get("watcher_policy")))
            continue
        if not applicable:
            items.append(_setting_item(entry, state="unset_definite", value=None, is_set=False, diagnostic=diagnostic))
        elif raw is not None:
            items.append(_setting_item(
                entry, state="explicit", value=raw, is_set=True, diagnostic=diagnostic,
            ))
        else:
            items.append(_setting_item(
                entry,
                state=entry["unset_state"],
                value=None,
                is_set=False,
                diagnostic=diagnostic,
            ))
    return items


def _policy_parent_item(entry: dict, raw: Any) -> ParameterItem:
    if isinstance(raw, dict):
        return _setting_item(
            entry,
            state="explicit",
            value=redact_params(raw, ()),
            is_set=True,
        )
    return _setting_item(entry, state="unset_definite", value=None, is_set=False)


def _step_settings(step: Mapping[str, Any]) -> list[ParameterItem]:
    items = []
    for entry in _entries("step"):
        key = entry["path"][-1]
        raw = step.get(key)
        if key == "retry":
            if raw in (None, 0):
                items.append(_setting_item(entry, state="unset_definite", value=None, is_set=False))
            else:
                items.append(_setting_item(entry, state="explicit", value=raw, is_set=True))
        elif key == "enabled":
            if raw is False:
                items.append(_setting_item(entry, state="explicit", value=False, is_set=True))
            else:
                items.append(_setting_item(entry, state="unset_definite", value=None, is_set=False))
        elif raw is None:
            items.append(_setting_item(entry, state=entry["unset_state"], value=None, is_set=False))
        else:
            items.append(_setting_item(entry, state="explicit", value=raw, is_set=True))
    return items


def _setting_item(
    entry: dict,
    *,
    state: str,
    value: Any,
    is_set: bool,
    diagnostic: str | None = None,
    factors: list[str] | None = None,
) -> ParameterItem:
    shown = value
    sensitive = False
    if isinstance(value, (dict, list)):
        shown = redact_params(value, ())
        sensitive = shown != value
    return ParameterItem(
        path=list(entry["path"] if entry["scope"] != "watcher" else ["watcher_policy", *entry["path"]]),
        label=entry["label"],
        meaning=entry["meaning"],
        unit=entry.get("unit") or None,
        cautions=None,
        source=None,
        state=state,  # type: ignore[arg-type]
        value=shown,
        sensitive=sensitive,
        is_set=is_set,
        fallback_chain=None if state == "explicit" else entry.get("fallback_chain"),
        authority=entry["semantic_anchors"][0] if entry.get("semantic_anchors") else None,
        decision_factors=factors or [],
        diagnostic=diagnostic,
        ui_editable=entry.get("ui_editable"),
        write_boundary=entry.get("write_boundary"),
    )


def _watcher_block(
    policy: Mapping[str, Any],
    layer: str,
    run_context: Any,
    claim_policy: Mapping[str, Any] | None,
) -> WatcherProjection:
    known = {entry["path"][-1] for entry in _entries("watcher")}
    items: list[ParameterItem] = []
    if layer == "L1":
        note = "主机覆盖待派发"
    elif layer == "L2":
        note = "冻结主机决定"
    else:
        note = _CLAIM_NOTE
    claim = claim_policy if isinstance(claim_policy, dict) else None
    for entry in _entries("watcher"):
        key = entry["path"][-1]
        factors: list[str] = []
        if layer in {"L1", "L2"}:
            factors.append(note)
        raw = policy.get(key, _MISSING)
        if layer == "L3" and claim is not None and key == "enabled" and claim.get("enabled") is False:
            items.append(_setting_item(
                entry, state="explicit", value=False, is_set=True, factors=[_CLAIM_NOTE],
            ))
            continue
        if raw is _MISSING:
            items.append(_setting_item(
                entry, state=entry["unset_state"], value=None, is_set=False, factors=factors,
            ))
        else:
            items.append(_setting_item(
                entry,
                state="explicit",
                value=raw,
                is_set=is_set_value(raw),
                factors=factors,
            ))
    for key, raw in policy.items():
        if key in known:
            continue
        sensitive_paths = union_sensitive_paths(None, ())
        shown = redact_params({key: raw}, sensitive_paths).get(key)
        items.append(ParameterItem(
            path=["watcher_policy", key],
            label=str(key),
            meaning="已保存的未知子键，只读保留，不截断",
            state="explicit",
            value=shown,
            sensitive=shown != raw,
            is_set=is_set_value(raw),
            ui_editable=False,
            write_boundary="只读",
            authority="plan.watcher_policy",
        ))
    frozen = extract_dispatch_host_watcher_admin_states(run_context) if layer != "L1" else None
    effective = None
    if layer == "L3":
        effective = redact_params(claim, ()) if isinstance(claim, dict) else None
    return WatcherProjection(
        note=note,
        items=items,
        effective_policy=effective,
        frozen_host_admin=frozen or None,
    )


def _ensure_pending(
    items: list[ParameterItem],
    pending: set[tuple],
    factors: Mapping[tuple, list[str]],
    script_name: str | None,
    version: str | None,
    schema: Mapping[str, Any],
    paths: set[tuple],
    authority: str,
) -> list[ParameterItem]:
    """注入才会填上的路径，合并结果里可能还没有叶子。"""
    seen = {tuple(item.path) for item in items}
    extra = list(items)
    for path in sorted(pending, key=lambda item: tuple(map(str, item))):
        if path in seen:
            continue
        doc = resolve_doc(
            load_param_docs(script_name or "").entries,
            path,
            version,
            schema_field_for_path(schema, path),
        )
        extra.append(ParameterItem(
            path=list(path),
            label=doc.label,
            meaning=doc.meaning,
            unit=doc.unit,
            cautions=doc.cautions,
            source=None,
            state="pending_dispatch",
            value=None,
            sensitive=_path_sensitive(path, paths, None),
            is_set=False,
            decision_factors=list(factors.get(path, [])),
            diagnostic=doc.diagnostic,
            authority=authority,
        ))
    return extra


def _leaf_items(
    script_name: str | None,
    version: str | None,
    schema: Mapping[str, Any],
    merged: Mapping[str, Any],
    sources: Mapping[str, str],
    paths: set[tuple],
    *,
    pending: set[tuple],
    factors: Mapping[tuple, list[str]],
    authority: str,
) -> list[ParameterItem]:
    items = []
    for path, raw in _iter_leaves(merged):
        if not path:
            continue
        doc = resolve_doc(
            load_param_docs(script_name or "").entries,
            path,
            version,
            schema_field_for_path(schema, path),
        )
        sensitive = _path_sensitive(path, paths, raw)
        source = sources.get(path[0]) if isinstance(path[0], str) else None
        if path in pending:
            items.append(ParameterItem(
                path=list(path),
                label=doc.label,
                meaning=doc.meaning,
                unit=doc.unit,
                cautions=doc.cautions,
                source=source if source in {
                    "schema_default", "script_default", "step_override", "dispatch_injection",
                } else None,
                state="pending_dispatch",
                value=None,
                sensitive=sensitive,
                is_set=False,
                base_value=None if not is_set_value(raw) else _shown(raw, path, paths, sensitive),
                decision_factors=list(factors.get(path, [])),
                diagnostic=doc.diagnostic,
                authority=authority,
            ))
            continue
        items.append(ParameterItem(
            path=list(path),
            label=doc.label,
            meaning=doc.meaning,
            unit=doc.unit,
            cautions=doc.cautions,
            source=source if source in {
                "schema_default", "script_default", "step_override", "dispatch_injection",
            } else None,
            state="explicit",
            value=_shown(raw, path, paths, sensitive),
            sensitive=sensitive,
            is_set=is_set_value(raw),
            diagnostic=doc.diagnostic,
            authority=authority,
        ))
    return items


def _l3_items(
    script_name: str | None,
    version: str | None,
    baseline: Mapping[str, Any],
    injected: Mapping[str, Any],
    job_params: Mapping[str, Any],
    sources: Mapping[Any, str],
    paths: set[tuple],
    wifi_changed: set[tuple],
    suite_changed: set[tuple],
) -> list[ParameterItem]:
    base_leaves = dict(_iter_leaves(baseline))
    inj_leaves = dict(_iter_leaves(injected))
    job_leaves = dict(_iter_leaves(job_params))
    all_paths = set(base_leaves) | set(inj_leaves) | set(job_leaves)
    items = []
    for path in sorted(all_paths, key=lambda item: tuple(map(str, item))):
        if not path:
            continue
        raw_job = job_leaves.get(path, _MISSING)
        raw_base = base_leaves.get(path, _MISSING)
        raw_inj = inj_leaves.get(path, _MISSING)
        changed = path in wifi_changed or path in suite_changed
        source: str | None
        diagnostic = None
        raw: Any
        if changed and raw_job is not _MISSING and raw_job == raw_inj and raw_job != raw_base:
            source = "dispatch_injection"
            raw = raw_job
        elif raw_job is not _MISSING and raw_job == raw_base:
            source = sources.get(path[0]) if path and isinstance(path[0], str) else None
            raw = raw_job
        elif raw_job is _MISSING and raw_base is not _MISSING:
            source = sources.get(path[0]) if path and isinstance(path[0], str) else None
            raw = raw_base
        else:
            source = None
            diagnostic = "来源不可追溯"
            raw = None if raw_job is _MISSING else raw_job
        if source not in {"schema_default", "script_default", "step_override", "dispatch_injection"}:
            source = None
        doc = resolve_doc(
            load_param_docs(script_name or "").entries,
            path,
            version,
            None,
        )
        sensitive = _path_sensitive(path, paths, None if raw is _MISSING else raw)
        items.append(ParameterItem(
            path=list(path),
            label=doc.label,
            meaning=doc.meaning,
            unit=doc.unit,
            cautions=doc.cautions,
            source=source,  # type: ignore[arg-type]
            state="explicit",
            value=_shown(None if raw is _MISSING else raw, path, paths, sensitive),
            sensitive=sensitive,
            is_set=raw is not _MISSING and is_set_value(raw),
            diagnostic=diagnostic or doc.diagnostic,
            authority="Job.pipeline_def" if source else "来源不可追溯",
            decision_factors=(
                ["WiFi/套件填空，显式值未被覆盖"] if source == "dispatch_injection" else []
            ),
        ))
    return items


def _shown(raw: Any, path: tuple, paths: set[tuple], sensitive: bool) -> Any:
    if sensitive and not isinstance(raw, (dict, list)):
        return None
    if isinstance(raw, (dict, list)):
        return redact_params(raw, {p[len(path):] for p in paths if p[:len(path)] == path})
    return raw


def _path_sensitive(path: tuple, paths: set[tuple], raw: Any) -> bool:
    if path in paths:
        return True
    if path and isinstance(path[-1], str) and path[-1].lower() in sensitive_query_keys():
        return True
    if isinstance(raw, (dict, list)) and redact_params(raw, ()) != raw:
        return True
    return False


def _sensitive_paths(script_name: str | None, version: str | None) -> set[tuple]:
    del version
    loaded = load_param_docs(script_name or "")
    return union_sensitive_paths(script_name, sensitive_registry_paths(loaded.entries))


def _source_map(schema: Mapping | None, defaults: Mapping | None, step_params: Mapping | None) -> dict[str, str]:
    sources: dict[str, str] = {}
    for key, field in (schema or {}).items():
        if isinstance(field, dict) and "default" in field:
            sources[key] = "schema_default"
    for key in defaults or {}:
        sources[key] = "script_default"
    for key in step_params or {}:
        sources[key] = "step_override"
    return sources


def _iter_leaves(value: Any, prefix: tuple = ()):
    if isinstance(value, dict):
        if not value:
            if prefix:
                yield prefix, value
            return
        for key, child in value.items():
            yield from _iter_leaves(child, prefix + (key,))
        return
    if isinstance(value, list):
        if not value:
            if prefix:
                yield prefix, value
            return
        for index, child in enumerate(value):
            yield from _iter_leaves(child, prefix + (index,))
        return
    if prefix:
        yield prefix, value


def _changed_leaf_paths(before: Mapping, after: Mapping) -> set[tuple]:
    left = dict(_iter_leaves(before))
    right = dict(_iter_leaves(after))
    changed = {path for path in right if left.get(path, _MISSING) != right[path]}
    changed.update(path for path in left if path not in right)
    return {path for path in changed if path}


def _apply_inject(lifecycle: Mapping, wifi: Mapping | None, suite: Mapping | None):
    before = {"lifecycle": deepcopy(dict(lifecycle))}
    after = deepcopy(before)
    if wifi:
        inject_wifi_params(after, dict(wifi))
    if suite:
        try:
            inject_suite_params(after, dict(suite))
        except PlanDispatchError:
            return before, after
    return before, after


def _params_of(pipeline: Mapping, step_id: Any) -> dict:
    for _phase, step in iter_lifecycle_steps(pipeline):
        if isinstance(step, dict) and step.get("step_id") == step_id:
            return step.get("params") or {}
    return {}


def _l1_lifecycle_params(plan: Any, steps: list[Any], metadata: Mapping) -> dict[str, dict] | None:
    lifecycle = _lifecycle_or_none(plan, steps, metadata)
    if lifecycle is None:
        return None
    return _lifecycle_param_map(lifecycle)


def _lifecycle_param_map(lifecycle: Mapping) -> dict[str, dict]:
    found = {}
    for _phase, step in iter_lifecycle_steps({"lifecycle": lifecycle}):
        if isinstance(step, dict):
            found[step.get("step_id")] = deepcopy(step.get("params") or {})
    return found


def _lifecycle_or_none(plan: Any, steps: list[Any], metadata: Mapping) -> dict | None:
    enabled = [step for step in steps if getattr(step, "enabled", True) is not False]
    if any((step.script_name, step.script_version) not in metadata for step in enabled):
        return None
    try:
        return build_lifecycle_from_steps(plan, steps, script_defaults(dict(metadata)))
    except PlanDispatchError:
        return None


def _snapshot_lifecycle(snapshot: Mapping) -> dict | None:
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("steps"), list):
        return None
    if not snapshot.get("plan"):
        return None
    try:
        return build_lifecycle_from_snapshot(deepcopy(dict(snapshot)))
    except PlanDispatchError:
        return None


def _l1_pending(plan: Any, lifecycle: Mapping | None):
    if not isinstance(lifecycle, dict):
        return {}, {}
    wifi = _changed_by_step(lifecycle, {"ssid": _PROBE + "ssid", "password": _PROBE + "pw"}, None)
    suite = {}
    if getattr(plan, "suite_id", None) is not None:
        suite = _changed_by_step(lifecycle, None, {
            "expected_testpoint_count": 1,
            "project": _PROBE + "project",
        })
    return wifi, suite


def _l2_pending(lifecycle: Mapping | None, run_context: Any):
    if not isinstance(lifecycle, dict):
        return {}, {}
    wifi = _changed_by_step(lifecycle, {"ssid": _PROBE + "ssid", "password": _PROBE + "pw"}, None)
    suite = {}
    frozen = run_context.get("dispatch_suite") if isinstance(run_context, dict) else None
    if isinstance(frozen, dict) and frozen.get("suite_id") is not None:
        suite = _changed_by_step(lifecycle, None, {
            "expected_testpoint_count": 1,
            "project": frozen.get("export_dir") or "legacy",
        })
    return wifi, suite


def _changed_by_step(lifecycle: Mapping, wifi: Mapping | None, suite: Mapping | None) -> dict[str, set[tuple]]:
    before, after = _apply_inject(lifecycle, wifi, suite)
    result: dict[str, set[tuple]] = {}
    before_steps = {
        step.get("step_id"): step for _p, step in iter_lifecycle_steps(before) if isinstance(step, dict)
    }
    for _phase, step in iter_lifecycle_steps(after):
        if not isinstance(step, dict):
            continue
        step_id = step.get("step_id")
        base = (before_steps.get(step_id) or {}).get("params") or {}
        result[step_id] = _changed_leaf_paths(base, step.get("params") or {})
    return result


def _suite_params_from_frozen(run_context: Any, lifecycle: Mapping | None, job_pipeline: Mapping) -> dict | None:
    frozen = run_context.get("dispatch_suite") if isinstance(run_context, dict) else None
    if not isinstance(frozen, dict) or frozen.get("suite_id") is None:
        return None
    params: dict[str, Any] = {"project": frozen.get("export_dir") or "legacy"}
    if not isinstance(lifecycle, dict):
        return params
    counts = []
    base_steps = {
        step.get("step_id"): step
        for _p, step in iter_lifecycle_steps({"lifecycle": lifecycle})
        if isinstance(step, dict)
    }
    for _phase, step in iter_lifecycle_steps(job_pipeline):
        if not isinstance(step, dict):
            continue
        base = (base_steps.get(step.get("step_id")) or {}).get("params") or {}
        job_params = step.get("params") or {}
        if "expected_testpoint_count" not in base and "expected_testpoint_count" in job_params:
            counts.append(job_params["expected_testpoint_count"])
    if counts and all(item == counts[0] for item in counts):
        params["expected_testpoint_count"] = counts[0]
    return params


def _l1_decisions(wifi_paths: Mapping, suite_paths: Mapping) -> list[DispatchDecision]:
    decisions = [DispatchDecision(
        kind="watcher_host",
        state="pending_dispatch",
        factors=["主机管控在 claim 时覆盖，本层不给出逐主机开关"],
    )]
    if any(wifi_paths.values()):
        decisions.append(DispatchDecision(
            kind="wifi",
            state="pending_dispatch",
            factors=["由所选 WiFi 池或自动选池在物化时按设备分配"],
        ))
    if any(suite_paths.values()):
        decisions.append(DispatchDecision(
            kind="suite",
            state="pending_dispatch",
            factors=["由绑定套件在派发时冻结"],
        ))
    return decisions


def _frozen_host_decisions(run_context: Any) -> list[DispatchDecision]:
    states = extract_dispatch_host_watcher_admin_states(run_context)
    decisions = []
    for host_id, active in states.items():
        decisions.append(DispatchDecision(
            kind="watcher_host",
            state="explicit",
            host_id=str(host_id),
            factors=[
                "冻结主机管控为 inactive，claim 时强制关闭"
                if not active
                else "冻结主机管控为 active"
            ],
        ))
    if not decisions:
        decisions.append(DispatchDecision(
            kind="watcher_host",
            state="pending_dispatch",
            factors=["快照没有冻结主机管控"],
        ))
    return decisions


def _plan_fields(plan: Any) -> dict[str, Any]:
    watcher = getattr(plan, "watcher_policy", None)
    return {
        "id": getattr(plan, "id", None),
        "suite_id": getattr(plan, "suite_id", None),
        "watcher_policy": deepcopy(watcher) if isinstance(watcher, dict) else watcher,
        **{key: getattr(plan, key, None) for key in _PLAN_NUMBER_KEYS},
    }


def _plan_fields_from_snapshot(snapshot: Any, plan_id: int | None) -> dict[str, Any]:
    plan = snapshot.get("plan") if isinstance(snapshot, dict) and isinstance(snapshot.get("plan"), dict) else {}
    watcher = plan.get("watcher_policy")
    return {
        "id": plan.get("id", plan_id),
        "suite_id": None,
        "watcher_policy": deepcopy(watcher) if isinstance(watcher, dict) else None,
        **{key: plan.get(key) for key in _PLAN_NUMBER_KEYS},
    }


def _snapshot_has_patrol(snapshot: Any) -> bool:
    steps = snapshot.get("steps") if isinstance(snapshot, dict) else None
    if not isinstance(steps, list):
        return False
    return any(
        isinstance(step, dict) and step.get("stage") == "patrol" and step.get("enabled") is not False
        for step in steps
    )


def _norm_step(step: Any) -> dict[str, Any]:
    enabled = _get(step, "enabled")
    retry = _get(step, "retry")
    params = _get(step, "params")
    return {
        "step_key": _get(step, "step_key"),
        "script_name": _get(step, "script_name"),
        "script_version": _get(step, "script_version"),
        "stage": _get(step, "stage"),
        "sort_order": _get(step, "sort_order") or 0,
        "enabled": True if enabled is None else bool(enabled),
        "timeout_seconds": _get(step, "timeout_seconds"),
        "stall_seconds": _get(step, "stall_seconds"),
        "retry": 0 if retry is None else retry,
        "params": deepcopy(params) if isinstance(params, dict) else None,
    }


def _norm_snapshot_step(raw: Mapping[str, Any]) -> dict[str, Any]:
    enabled = raw.get("enabled", True)
    return {
        "step_key": raw.get("step_key"),
        "script_name": raw.get("script_name"),
        "script_version": raw.get("script_version"),
        "stage": raw.get("stage"),
        "sort_order": raw.get("sort_order") or 0,
        "enabled": True if enabled is None else bool(enabled),
        "timeout_seconds": raw.get("timeout_seconds"),
        "stall_seconds": raw.get("stall_seconds"),
        "retry": raw.get("retry", 0),
        "params": deepcopy(raw.get("params")) if isinstance(raw.get("params"), dict) else None,
    }


def _has_enabled_patrol(steps: Iterable[Mapping[str, Any]]) -> bool:
    return any(step.get("stage") == "patrol" and step.get("enabled") is not False for step in steps)


def _get(obj: Any, name: str) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(name)
    return getattr(obj, name, None)


def _reject_probe(value: Any) -> None:
    dumped = value.model_dump(mode="json") if hasattr(value, "model_dump") else value
    if contains_text(dumped, _PROBE):
        raise RuntimeError("projection leaked an injection probe value")
