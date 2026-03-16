"""Workflow dispatcher: fan-out WorkflowRun → JobInstances per (device × TaskTemplate)."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.pipeline_validator import validate_pipeline_def
from backend.models.enums import JobStatus
from backend.models.job import JobInstance, TaskTemplate
from backend.models.tool import Tool
from backend.models.workflow import WorkflowDefinition, WorkflowRun

logger = logging.getLogger(__name__)


class DispatchError(Exception):
    pass


async def dispatch_workflow(
    workflow_def_id: int,
    device_ids: List[int],
    failure_threshold: float,
    triggered_by: str,
    db: AsyncSession,
) -> WorkflowRun:
    """
    1. Load WorkflowDefinition + TaskTemplates
    2. Validate all tool_ids in pipeline_defs are active
    3. Create WorkflowRun (RUNNING)
    4. Create JobInstance per (device_id × TaskTemplate)
    5. Return WorkflowRun
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

    await _validate_tool_references(templates, db)

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
    # Device locking is deferred to the claim endpoint (agent_api.get_pending_jobs)
    # which atomically transitions PENDING → RUNNING + acquires the device lock.
    # Pre-locking here would use WorkflowRun.id as the lock owner, but claim/complete
    # use JobInstance.id, causing an owner semantic mismatch.
    for device_id in device_ids:
        for template in templates:
            job = JobInstance(
                workflow_run_id=run.id,
                task_template_id=template.id,
                device_id=device_id,
                host_id=device_host_map.get(device_id),
                status=JobStatus.PENDING.value,
                pipeline_def=template.pipeline_def,
                created_at=now,
                updated_at=now,
            )
            db.add(job)

    await db.commit()
    await db.refresh(run)

    job_count = len(device_ids) * len(templates)
    logger.info(
        "dispatched workflow_def=%d run=%d devices=%d templates=%d jobs=%d",
        workflow_def_id, run.id, len(device_ids), len(templates), job_count,
    )
    return run


async def _validate_tool_references(templates: list, db: AsyncSession) -> None:
    """Collect all tool_ids from pipeline_defs and verify they exist and are active."""
    tool_ids: set[int] = set()
    for t in templates:
        is_valid, errors = validate_pipeline_def(t.pipeline_def or {})
        if not is_valid:
            raise DispatchError(
                f"Invalid pipeline_def for template '{t.name}': {'; '.join(errors)}"
            )

        stages = (t.pipeline_def or {}).get("stages", {})
        for steps in stages.values():
            for step in steps:
                action = step.get("action", "")
                if action.startswith("tool:"):
                    try:
                        tool_ids.add(int(action.split(":", 1)[1]))
                    except (IndexError, ValueError):
                        pass

    if not tool_ids:
        return

    active_ids = set(
        (await db.execute(
            select(Tool.id).where(Tool.id.in_(tool_ids), Tool.is_active.is_(True))
        )).scalars().all()
    )
    missing = tool_ids - active_ids
    if missing:
        raise DispatchError(f"Tools not found or inactive: {sorted(missing)}")
