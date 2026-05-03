"""Script execution facade over the existing WorkflowRun/JobInstance path."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.models.enums import JobStatus
from backend.models.host import Device
from backend.models.job import JobArtifact, JobInstance, StepTrace, TaskTemplate
from backend.models.script import Script
from backend.models.script_sequence import ScriptSequence
from backend.models.workflow import WorkflowDefinition, WorkflowRun

SYSTEM_WORKFLOW_NAME = "__script_execution__"
SYSTEM_TEMPLATE_NAME = "__script_sequence__"

_ACTIVE_JOB_STATUSES = {JobStatus.PENDING.value, JobStatus.RUNNING.value, JobStatus.UNKNOWN.value}


def validate_on_failure(value: str) -> str:
    if value != "stop":
        raise HTTPException(status_code=422, detail="only on_failure='stop' is supported")
    return value


def normalize_script_items(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for raw in items or []:
        script_name = str(raw.get("script_name") or "").strip()
        version = str(raw.get("version") or "").strip()
        if not script_name:
            raise HTTPException(status_code=422, detail="script_name is required")
        if not version:
            raise HTTPException(status_code=422, detail=f"version is required for {script_name}")
        params = raw.get("params") or {}
        if not isinstance(params, dict):
            raise HTTPException(status_code=422, detail=f"params must be an object for {script_name}")
        timeout_seconds = int(raw.get("timeout_seconds") or 3600)
        retry = int(raw.get("retry") or 0)
        if timeout_seconds < 1:
            raise HTTPException(status_code=422, detail=f"timeout_seconds must be >= 1 for {script_name}")
        if retry < 0 or retry > 10:
            raise HTTPException(status_code=422, detail=f"retry must be between 0 and 10 for {script_name}")
        normalized.append(
            {
                "script_name": script_name,
                "version": version,
                "params": params,
                "timeout_seconds": timeout_seconds,
                "retry": retry,
            }
        )
    if not normalized:
        raise HTTPException(status_code=422, detail="at least one script item is required")
    return normalized


def validate_active_scripts(db: Session, items: list[dict[str, Any]]) -> None:
    missing: list[str] = []
    for item in items:
        exists = (
            db.query(Script.id)
            .filter(
                Script.name == item["script_name"],
                Script.version == item["version"],
                Script.is_active.is_(True),
            )
            .first()
        )
        if exists is None:
            missing.append(f"{item['script_name']}:{item['version']}")
    if missing:
        raise HTTPException(status_code=400, detail=f"scripts not found or inactive: {', '.join(missing)}")


def synthesize_script_pipeline(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "stages": {
            "execute": [
                {
                    "step_id": f"script_{index}_{item['script_name']}",
                    "action": f"script:{item['script_name']}",
                    "version": item["version"],
                    "params": item.get("params") or {},
                    "timeout_seconds": item.get("timeout_seconds") or 3600,
                    "retry": item.get("retry", 0),
                    "enabled": True,
                }
                for index, item in enumerate(items)
            ]
        }
    }


def ensure_system_anchor(db: Session) -> tuple[WorkflowDefinition, TaskTemplate]:
    workflow = (
        db.query(WorkflowDefinition)
        .filter(
            WorkflowDefinition.name == SYSTEM_WORKFLOW_NAME,
            WorkflowDefinition.created_by == "system",
        )
        .first()
    )
    now = datetime.now(timezone.utc)
    if workflow is None:
        workflow = WorkflowDefinition(
            name=SYSTEM_WORKFLOW_NAME,
            description="System workflow anchor for script execution facade",
            failure_threshold=0.0,
            created_by="system",
            created_at=now,
            updated_at=now,
        )
        db.add(workflow)
        db.flush()

    template = (
        db.query(TaskTemplate)
        .filter(
            TaskTemplate.workflow_definition_id == workflow.id,
            TaskTemplate.name == SYSTEM_TEMPLATE_NAME,
        )
        .first()
    )
    if template is None:
        template = TaskTemplate(
            workflow_definition_id=workflow.id,
            name=SYSTEM_TEMPLATE_NAME,
            pipeline_def=synthesize_script_pipeline(
                [
                    {
                        "script_name": "placeholder",
                        "version": "0.0.0",
                        "params": {},
                        "timeout_seconds": 1,
                        "retry": 0,
                    }
                ]
            ),
            sort_order=0,
            created_at=now,
        )
        db.add(template)
        db.flush()
    return workflow, template


def resolve_execution_items(
    db: Session,
    *,
    sequence_id: int | None,
    items: list[dict[str, Any]] | None,
) -> tuple[list[dict[str, Any]], int | None]:
    if items:
        normalized = normalize_script_items(items)
        validate_active_scripts(db, normalized)
        return normalized, sequence_id
    if sequence_id is None:
        raise HTTPException(status_code=422, detail="sequence_id or items is required")
    sequence = db.get(ScriptSequence, sequence_id)
    if sequence is None:
        raise HTTPException(status_code=404, detail="script sequence not found")
    normalized = normalize_script_items(sequence.items or [])
    validate_active_scripts(db, normalized)
    return normalized, sequence_id


def create_script_execution(
    db: Session,
    *,
    items: list[dict[str, Any]],
    device_ids: list[int],
    sequence_id: int | None,
    on_failure: str,
    triggered_by: str = "script_execution",
) -> dict[str, Any]:
    on_failure = validate_on_failure(on_failure)
    if not device_ids:
        raise HTTPException(status_code=422, detail="at least one device is required")

    devices = db.query(Device).filter(Device.id.in_(device_ids)).all()
    by_id = {device.id: device for device in devices}
    missing_devices = [device_id for device_id in device_ids if device_id not in by_id]
    if missing_devices:
        raise HTTPException(status_code=400, detail=f"devices not found: {missing_devices}")

    # Phase 5 guard: reject if any target device already has an active job
    conflict = (
        db.query(JobInstance.device_id)
        .filter(
            JobInstance.device_id.in_(device_ids),
            JobInstance.status.in_(_ACTIVE_JOB_STATUSES),
        )
        .first()
    )
    if conflict is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Device {conflict.device_id} already has an active job (PENDING/RUNNING/UNKNOWN). "
            "Wait for it to reach a terminal state or abort it first.",
        )

    workflow, template = ensure_system_anchor(db)
    pipeline_def = synthesize_script_pipeline(items)
    now = datetime.now(timezone.utc)
    run = WorkflowRun(
        workflow_definition_id=workflow.id,
        status="RUNNING",
        failure_threshold=0.0,
        triggered_by=triggered_by,
        started_at=now,
        result_summary={
            "mode": "script_execution",
            "sequence_id": sequence_id,
            "items": items,
            "on_failure": on_failure,
        },
    )
    db.add(run)
    db.flush()

    job_ids: list[int] = []
    for device_id in device_ids:
        device = by_id[device_id]
        job = JobInstance(
            workflow_run_id=run.id,
            task_template_id=template.id,
            device_id=device.id,
            host_id=device.host_id,
            status=JobStatus.PENDING.value,
            pipeline_def=pipeline_def,
            created_at=now,
            updated_at=now,
        )
        db.add(job)
        db.flush()
        job_ids.append(job.id)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Device already has an active job (concurrent conflict). "
            "Wait for it to reach a terminal state or abort it first.",
        )
    return {
        "workflow_run_id": run.id,
        "job_ids": job_ids,
        "device_count": len(device_ids),
        "step_count": len(items),
    }


def script_name_from_step(step: dict[str, Any]) -> str:
    action = str(step.get("action") or "")
    if action.startswith("script:"):
        return action.split(":", 1)[1]
    return action


def script_execution_detail(db: Session, run_id: int) -> dict[str, Any]:
    run = db.get(WorkflowRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="script execution not found")
    summary = run.result_summary or {}
    if summary.get("mode") != "script_execution":
        raise HTTPException(status_code=404, detail="script execution not found")

    jobs = (
        db.query(JobInstance)
        .filter(JobInstance.workflow_run_id == run.id)
        .order_by(JobInstance.id)
        .all()
    )
    payload_jobs = []
    for job in jobs:
        traces = {
            trace.step_id: trace
            for trace in (
                db.query(StepTrace)
                .filter(StepTrace.job_id == job.id)
                .order_by(StepTrace.created_at, StepTrace.id)
                .all()
            )
        }
        artifacts = (
            db.query(JobArtifact)
            .filter(JobArtifact.job_id == job.id)
            .order_by(JobArtifact.created_at, JobArtifact.id)
            .all()
        )
        steps = []
        for step in (job.pipeline_def or {}).get("stages", {}).get("execute", []) or []:
            trace = traces.get(step.get("step_id"))
            steps.append(
                {
                    "step_id": step.get("step_id"),
                    "script_name": script_name_from_step(step),
                    "version": step.get("version"),
                    "params": step.get("params") or {},
                    "timeout_seconds": step.get("timeout_seconds"),
                    "retry": step.get("retry", 0),
                    "status": trace.status if trace else "PENDING",
                    "output": trace.output if trace else None,
                    "error_message": trace.error_message if trace else None,
                }
            )
        payload_jobs.append(
            {
                "id": job.id,
                "device_id": job.device_id,
                "device_serial": job.device.serial if job.device else None,
                "device_model": job.device.model if job.device else None,
                "host_id": job.host_id,
                "host_name": (job.host.name or job.host.hostname) if job.host else None,
                "status": job.status,
                "status_reason": job.status_reason,
                "started_at": job.started_at,
                "ended_at": job.ended_at,
                "watcher_capability": job.watcher_capability,
                "log_signal_count": job.log_signal_count,
                "steps": steps,
                "artifacts": [
                    {
                        "id": artifact.id,
                        "storage_uri": artifact.storage_uri,
                        "artifact_type": artifact.artifact_type,
                        "size_bytes": artifact.size_bytes,
                        "checksum": artifact.checksum,
                        "created_at": artifact.created_at,
                    }
                    for artifact in artifacts
                ],
            }
        )

    return {
        "workflow_run_id": run.id,
        "mode": "script_execution",
        "status": run.status,
        "sequence_id": summary.get("sequence_id"),
        "items": summary.get("items") or [],
        "on_failure": summary.get("on_failure", "stop"),
        "jobs": payload_jobs,
    }
