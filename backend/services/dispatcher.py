"""Workflow dispatcher: fan-out WorkflowRun → JobInstances per (device × TaskTemplate)."""

from __future__ import annotations

import logging
from copy import deepcopy
from datetime import datetime
from typing import Any, List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.pipeline_validator import validate_pipeline_def
from backend.models.enums import JobStatus
from backend.models.job import JobInstance, TaskTemplate
from backend.models.resource_pool import ResourcePool
from backend.models.script import Script
from backend.models.tool import Tool
from backend.models.workflow import WorkflowDefinition, WorkflowRun
from backend.services.resource_pool import (
    AllocationError,
    allocate_devices,
    create_allocations,
)

logger = logging.getLogger(__name__)


class DispatchError(Exception):
    pass


def _resolve_pipeline(setup: dict | None, task: dict, teardown: dict | None) -> dict:
    """Compose workflow-level setup/teardown with a task pipeline.

    When workflow-level pipelines are absent, return the task pipeline unchanged
    so existing TaskTemplate semantics are preserved.
    """
    if setup is None and teardown is None:
        return task

    task_stages = (task or {}).get("stages", {})
    setup_stages = (setup or {}).get("stages", {})
    teardown_stages = (teardown or {}).get("stages", {})

    return {
        "stages": {
            "prepare": list(setup_stages.get("prepare") or [])
            + list(task_stages.get("prepare") or []),
            "execute": list(task_stages.get("execute") or []),
            "post_process": list(task_stages.get("post_process") or [])
            + list(teardown_stages.get("post_process") or []),
        }
    }


def _apply_step_overrides(
    pipeline: dict,
    template_name: str,
    overrides: list[dict] | None,
) -> dict:
    """Return a copied pipeline with matching dispatch-time step overrides applied."""
    resolved = deepcopy(pipeline or {"stages": {}})
    if not overrides:
        return resolved

    stages = resolved.setdefault("stages", {})
    for override in overrides:
        if override.get("template_name") != template_name:
            continue
        stage = override.get("stage")
        step_id = override.get("step_id")
        if not stage or not step_id:
            continue
        for step in stages.get(stage) or []:
            if step.get("step_id") != step_id:
                continue
            if override.get("params") is not None:
                params = dict(step.get("params") or {})
                params.update(override["params"])
                step["params"] = params
            for field in ("timeout_seconds", "retry", "enabled"):
                if override.get(field) is not None:
                    step[field] = override[field]
    return resolved


def _build_template_preview(template_name: str, pipeline: dict) -> dict[str, Any]:
    stages = (pipeline or {}).get("stages", {})
    steps = [step for stage_steps in stages.values() for step in (stage_steps or [])]
    disabled = sum(1 for step in steps if step.get("enabled") is False)
    return {
        "name": template_name,
        "resolved_pipeline": pipeline,
        "total_steps": len(steps),
        "disabled_steps": disabled,
        "executable_steps": len(steps) - disabled,
    }


def _resolve_template_pipeline(
    wf_def: WorkflowDefinition,
    template: TaskTemplate,
    step_overrides: list[dict] | None = None,
) -> dict:
    resolved_pipeline = _resolve_pipeline(
        wf_def.setup_pipeline,
        template.pipeline_def,
        wf_def.teardown_pipeline,
    )
    return _apply_step_overrides(resolved_pipeline, template.name, step_overrides)


def _inject_wifi_params(pipeline: dict, wifi_params: dict | None) -> dict:
    """Inject WiFi pool SSID/password into connect_wifi step params."""
    if not wifi_params or not wifi_params.get("ssid"):
        return pipeline

    for stage_steps in (pipeline or {}).get("stages", {}).values():
        for step in (stage_steps or []):
            action = step.get("action", "")
            if "connect_wifi" not in action:
                continue
            params = dict(step.get("params") or {})
            if not params.get("ssid"):
                params["ssid"] = wifi_params["ssid"]
            if not params.get("password"):
                params["password"] = wifi_params.get("password", "")
            step["params"] = params

    return pipeline


async def preview_workflow_dispatch(
    workflow_def_id: int,
    device_ids: List[int],
    failure_threshold: float,
    db: AsyncSession,
    step_overrides: list[dict] | None = None,
) -> dict[str, Any]:
    wf_def = await db.get(WorkflowDefinition, workflow_def_id)
    if wf_def is None:
        raise DispatchError(f"WorkflowDefinition {workflow_def_id} not found")

    templates_result = await db.execute(
        select(TaskTemplate)
        .where(TaskTemplate.workflow_definition_id == workflow_def_id)
        .order_by(TaskTemplate.sort_order)
    )
    templates = templates_result.scalars().all()
    if not templates:
        raise DispatchError(f"WorkflowDefinition {workflow_def_id} has no TaskTemplates")

    await _validate_tool_references(wf_def, templates, db, step_overrides)

    template_previews = []
    executable_steps = 0
    for template in templates:
        pipeline = _resolve_template_pipeline(wf_def, template, step_overrides)
        preview = _build_template_preview(template.name, pipeline)
        preview["id"] = template.id
        preview["sort_order"] = template.sort_order
        executable_steps += preview["executable_steps"]
        template_previews.append(preview)

    return {
        "workflow_definition_id": workflow_def_id,
        "failure_threshold": failure_threshold,
        "device_ids": device_ids,
        "device_count": len(device_ids),
        "template_count": len(template_previews),
        "job_count": len(device_ids) * len(template_previews),
        "executable_steps_per_device": executable_steps,
        "templates": template_previews,
    }


async def dispatch_workflow(
    workflow_def_id: int,
    device_ids: List[int],
    failure_threshold: float,
    triggered_by: str,
    db: AsyncSession,
    step_overrides: list[dict] | None = None,
) -> WorkflowRun:
    """
    1. Load WorkflowDefinition + TaskTemplates
    2. Validate all tool_ids in pipeline_defs are active
    3. Create WorkflowRun (RUNNING)
    4. Allocate WiFi pools per device (ResourcePool decision layer)
    5. Create JobInstance per (device_id × TaskTemplate) with WiFi params injected
    6. Return WorkflowRun
    """
    wf_def = await db.get(WorkflowDefinition, workflow_def_id)
    if wf_def is None:
        raise DispatchError(f"WorkflowDefinition {workflow_def_id} not found")

    templates_result = await db.execute(
        select(TaskTemplate)
        .where(TaskTemplate.workflow_definition_id == workflow_def_id)
        .order_by(TaskTemplate.sort_order)
    )
    templates = templates_result.scalars().all()
    if not templates:
        raise DispatchError(f"WorkflowDefinition {workflow_def_id} has no TaskTemplates")

    await _validate_tool_references(wf_def, templates, db, step_overrides)

    # Pre-fetch device → host mapping so each JobInstance can be pre-assigned
    from backend.models.host import Device
    device_rows = (await db.execute(
        select(Device.id, Device.host_id)
        .where(Device.id.in_(device_ids))
    )).all()
    device_host_map = {row.id: row.host_id for row in device_rows}

    # Warn about devices without host assignment (Agent won't pick them up)
    orphan_devices = [did for did in device_ids if not device_host_map.get(did)]
    if orphan_devices:
        logger.warning(
            "dispatch_devices_without_host: device_ids=%s — Agent may not pick up these jobs",
            orphan_devices,
        )

    # ── ResourcePool: allocate WiFi per device ──
    wifi_allocations: Dict[int, dict] = {}
    try:
        assignments = await allocate_devices(db, device_ids, resource_type="wifi")
        for device_id, (_pool, alloc_params) in assignments.items():
            wifi_allocations[device_id] = alloc_params
        logger.info(
            "dispatch_wifi_allocated: devices=%d pools_used=%d",
            len(device_ids),
            len({a[0].id for a in assignments.values()}),
        )
    except AllocationError as exc:
        logger.warning("dispatch_wifi_allocation_skipped: %s", exc)

    run = WorkflowRun(
        workflow_definition_id=workflow_def_id,
        status="RUNNING",
        failure_threshold=failure_threshold,
        triggered_by=triggered_by,
        started_at=datetime.utcnow(),
    )
    db.add(run)
    await db.flush()

    now = datetime.utcnow()
    job_device_pairs: Dict[int, int] = {}
    for device_id in device_ids:
        for template in templates:
            resolved_pipeline = _resolve_template_pipeline(wf_def, template, step_overrides)
            # Inject WiFi allocation into pipeline step params
            wifi_params = wifi_allocations.get(device_id)
            if wifi_params:
                resolved_pipeline = _inject_wifi_params(resolved_pipeline, wifi_params)

            job = JobInstance(
                workflow_run_id=run.id,
                task_template_id=template.id,
                device_id=device_id,
                host_id=device_host_map.get(device_id),
                status=JobStatus.PENDING.value,
                pipeline_def=resolved_pipeline,
                created_at=now,
                updated_at=now,
            )
            db.add(job)
            await db.flush()
            job_device_pairs[job.id] = device_id

    # Persist ResourceAllocation records
    if wifi_allocations:
        assignment_refs = {
            did: (
                (await db.execute(
                    select(ResourcePool).where(ResourcePool.id == wifi_allocations[did]["pool_id"])
                )).scalar(),
                wifi_allocations[did],
            )
            for did in wifi_allocations
        }
        await create_allocations(db, assignment_refs, job_device_pairs)

    await db.commit()
    await db.refresh(run)

    job_count = len(device_ids) * len(templates)
    logger.info(
        "dispatched workflow_def=%d run=%d devices=%d templates=%d jobs=%d",
        workflow_def_id, run.id, len(device_ids), len(templates), job_count,
    )
    return run


async def _validate_tool_references(
    wf_def: WorkflowDefinition,
    templates: list,
    db: AsyncSession,
    step_overrides: list[dict] | None = None,
) -> None:
    """Collect tool/script refs from pipeline_defs and verify active catalog entries."""
    tool_ids: set[int] = set()
    script_refs: set[tuple[str, str]] = set()
    for t in templates:
        pipeline_def = _resolve_template_pipeline(wf_def, t, step_overrides)
        is_valid, errors = validate_pipeline_def(pipeline_def or {})
        if not is_valid:
            raise DispatchError(
                f"Invalid pipeline_def for template '{t.name}': {'; '.join(errors)}"
            )

        stages = (pipeline_def or {}).get("stages", {})
        for steps in stages.values():
            for step in steps:
                if step.get("enabled") is False:
                    continue
                action = step.get("action", "")
                if action.startswith("tool:"):
                    try:
                        tool_ids.add(int(action.split(":", 1)[1]))
                    except (IndexError, ValueError):
                        pass
                elif action.startswith("script:"):
                    name = action.split(":", 1)[1]
                    version = step.get("version", "")
                    if name and version:
                        script_refs.add((name, version))

    if tool_ids:
        active_ids = set(
            (await db.execute(
                select(Tool.id).where(Tool.id.in_(tool_ids), Tool.is_active.is_(True))
            )).scalars().all()
        )
        missing = tool_ids - active_ids
        if missing:
            raise DispatchError(f"Tools not found or inactive: {sorted(missing)}")

    if not script_refs:
        return

    script_names = {name for name, _ in script_refs}
    active_scripts = set(
        (await db.execute(
            select(Script.name, Script.version).where(
                Script.name.in_(script_names),
                Script.is_active.is_(True),
            )
        )).all()
    )
    missing_scripts = script_refs - active_scripts
    if missing_scripts:
        formatted = [f"{name}:{version}" for name, version in sorted(missing_scripts)]
        raise DispatchError(f"Scripts not found or inactive: {formatted}")
