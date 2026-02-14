import asyncio
import json
import logging
import os
import shlex
import tarfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from urllib.parse import unquote, urlparse

import paramiko
from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse
from sqlalchemy import text
from sqlalchemy.orm import Session, selectinload

from backend.core.database import get_db
from backend.core.task_templates import list_templates as list_core_templates
from backend.models.schemas import Device, DeviceStatus, Host, HostStatus, LogArtifact, RunStatus, Task, TaskRun, TaskStatus
from backend.api.schemas import (
    AgentLogOut,
    AgentLogQuery,
    DeviceLiteOut,
    HostLiteOut,
    JiraDraftOut,
    LogArtifactIn,
    RiskAlertOut,
    RunAgentOut,
    RunCompleteIn,
    RunOut,
    RunReportOut,
    RunUpdate,
    TaskCreate,
    TaskDispatch,
    TaskTemplateOut,
    TaskOut,
)
from backend.api.routes.auth import get_current_active_user, User, verify_agent_secret

router = APIRouter(prefix="/api/v1", tags=["tasks"])
logger = logging.getLogger(__name__)

DEVICE_LOCK_LEASE_SECONDS = int(os.getenv("DEVICE_LOCK_LEASE_SECONDS", "600"))
REPORT_ALERT_ANR_THRESHOLD = int(os.getenv("RUN_REPORT_ALERT_ANR_THRESHOLD", "1"))
REPORT_ALERT_CRASH_THRESHOLD = int(os.getenv("RUN_REPORT_ALERT_CRASH_THRESHOLD", "1"))
REPORT_ALERT_RESTART_THRESHOLD = int(os.getenv("RUN_REPORT_ALERT_RESTART_THRESHOLD", "2"))
REPORT_JIRA_PROJECT_KEY = os.getenv("RUN_REPORT_JIRA_PROJECT_KEY", "STABILITY")
REPORT_JIRA_TEMPLATE_JSON = os.getenv("RUN_REPORT_JIRA_TEMPLATE_JSON", "").strip()

TASK_STATUS_TRANSITIONS = {
    TaskStatus.PENDING: {TaskStatus.QUEUED, TaskStatus.CANCELED},
    TaskStatus.QUEUED: {TaskStatus.RUNNING, TaskStatus.CANCELED, TaskStatus.FAILED},
    TaskStatus.RUNNING: {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELED},
    TaskStatus.COMPLETED: set(),
    TaskStatus.FAILED: set(),
    TaskStatus.CANCELED: set(),
}

RUN_STATUS_TRANSITIONS = {
    RunStatus.QUEUED: {RunStatus.DISPATCHED, RunStatus.FAILED, RunStatus.CANCELED},
    RunStatus.DISPATCHED: {RunStatus.RUNNING, RunStatus.FAILED, RunStatus.CANCELED},
    RunStatus.RUNNING: {RunStatus.FINISHED, RunStatus.FAILED, RunStatus.CANCELED},
    RunStatus.FINISHED: set(),
    RunStatus.FAILED: set(),
    RunStatus.CANCELED: set(),
}

RUN_STATUS_ALIASES = {
    "COMPLETED": RunStatus.FINISHED.value,
    "CANCELLED": RunStatus.CANCELED.value,
}


def _normalize_run_status(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    normalized = value.strip().upper()
    return RUN_STATUS_ALIASES.get(normalized, normalized)


def _artifact_download_target(storage_uri: str) -> Dict[str, str]:
    parsed = urlparse(storage_uri)
    scheme = parsed.scheme.lower()
    if scheme in {"http", "https"}:
        return {"kind": "redirect", "url": storage_uri}

    if scheme != "file":
        raise HTTPException(status_code=400, detail=f"unsupported artifact scheme: {scheme or 'empty'}")

    if parsed.netloc and parsed.path:
        local_path = Path(f"//{parsed.netloc}{unquote(parsed.path)}")
    elif parsed.netloc and not parsed.path:
        local_path = Path(unquote(parsed.netloc))
    else:
        local_path = Path(unquote(parsed.path))

    if not local_path.exists() or not local_path.is_file():
        raise HTTPException(status_code=404, detail=f"artifact file not found: {local_path}")
    return {"kind": "local", "path": str(local_path)}


def _artifact_local_path(storage_uri: str) -> Optional[Path]:
    parsed = urlparse(storage_uri)
    if parsed.scheme.lower() != "file":
        return None
    if parsed.netloc and parsed.path:
        return Path(f"//{parsed.netloc}{unquote(parsed.path)}")
    if parsed.netloc and not parsed.path:
        return Path(unquote(parsed.netloc))
    return Path(unquote(parsed.path))


def _load_json_file(path: Path) -> Optional[Dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
        return payload if isinstance(payload, dict) else None
    except Exception:
        return None


def _load_risk_summary_from_tar(path: Path) -> Optional[Dict[str, Any]]:
    try:
        with tarfile.open(path, "r:*") as tar:
            for member in tar.getmembers():
                if member.isfile() and member.name.endswith("risk_summary.json"):
                    handle = tar.extractfile(member)
                    if not handle:
                        continue
                    payload = json.loads(handle.read().decode("utf-8", errors="ignore"))
                    return payload if isinstance(payload, dict) else None
    except Exception:
        logger.warning("risk_summary_from_tar_failed", extra={"path": str(path)})
    return None


def _load_risk_summary_from_artifacts(artifacts: List[LogArtifact]) -> Optional[Dict[str, Any]]:
    if not artifacts:
        return None
    ordered = sorted(
        artifacts,
        key=lambda item: item.created_at or datetime.min,
        reverse=True,
    )
    for artifact in ordered:
        local_path = _artifact_local_path(artifact.storage_uri)
        if not local_path or not local_path.exists() or not local_path.is_file():
            continue

        if local_path.name == "risk_summary.json":
            payload = _load_json_file(local_path)
            if payload:
                return payload
            continue

        if local_path.suffix == ".tgz" or local_path.suffixes[-2:] == [".tar", ".gz"]:
            tar_summary = _load_risk_summary_from_tar(local_path)
            if tar_summary:
                return tar_summary
            if local_path.suffix == ".tgz":
                base_name = local_path.name[:-4]
            else:
                base_name = local_path.name[:-7]
            sidecar = local_path.parent / base_name / "risk_summary.json"
            payload = _load_json_file(sidecar)
            if payload:
                return payload
    return None


def _parse_run_log_summary(log_summary: Optional[str]) -> Dict[str, Any]:
    if not log_summary:
        return {}
    raw = str(log_summary).strip()
    if not raw:
        return {}
    metrics: Dict[str, Any] = {}
    for part in raw.split(";"):
        item = part.strip()
        if not item or "=" not in item:
            continue
        key, value = item.split("=", 1)
        normalized_key = key.strip()
        normalized_value = value.strip()
        if not normalized_key:
            continue
        try:
            metrics[normalized_key] = int(normalized_value)
        except ValueError:
            metrics[normalized_key] = normalized_value
    return metrics


def _build_risk_alerts(risk_summary: Optional[Dict[str, Any]], summary_metrics: Dict[str, Any]) -> List[RiskAlertOut]:
    if not isinstance(risk_summary, dict):
        return []

    counts = risk_summary.get("counts")
    if not isinstance(counts, dict):
        counts = {}
    by_type = counts.get("by_type")
    if not isinstance(by_type, dict):
        by_type = {}

    alerts: List[RiskAlertOut] = []
    risk_level = str(risk_summary.get("risk_level", "")).upper()
    if risk_level == "HIGH":
        alerts.append(
            RiskAlertOut(
                code="RISK_LEVEL_HIGH",
                severity="HIGH",
                message="Risk level is HIGH",
                metric="risk_level",
            )
        )

    anr_count = int(by_type.get("ANR", 0) or 0)
    if anr_count >= REPORT_ALERT_ANR_THRESHOLD:
        alerts.append(
            RiskAlertOut(
                code="ANR_DETECTED",
                severity="HIGH",
                message=f"ANR count reached {anr_count}",
                metric="ANR",
                value=anr_count,
                threshold=REPORT_ALERT_ANR_THRESHOLD,
            )
        )

    crash_count = int(by_type.get("CRASH", 0) or 0)
    if crash_count >= REPORT_ALERT_CRASH_THRESHOLD:
        alerts.append(
            RiskAlertOut(
                code="CRASH_DETECTED",
                severity="HIGH",
                message=f"CRASH count reached {crash_count}",
                metric="CRASH",
                value=crash_count,
                threshold=REPORT_ALERT_CRASH_THRESHOLD,
            )
        )

    restart_count = int(summary_metrics.get("restarts", counts.get("restart_count", 0)) or 0)
    if restart_count >= REPORT_ALERT_RESTART_THRESHOLD:
        alerts.append(
            RiskAlertOut(
                code="RESTART_FREQUENT",
                severity="MEDIUM",
                message=f"restart count reached {restart_count}",
                metric="restart_count",
                value=restart_count,
                threshold=REPORT_ALERT_RESTART_THRESHOLD,
            )
        )
    return alerts


def _compose_run_report(db: Session, run_id: int) -> RunReportOut:
    run = (
        db.query(TaskRun)
        .options(selectinload(TaskRun.artifacts))
        .filter(TaskRun.id == run_id)
        .first()
    )
    if not run:
        raise HTTPException(status_code=404, detail="run not found")

    task = db.get(Task, run.task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    host = db.get(Host, run.host_id)
    device = db.get(Device, run.device_id)

    if hasattr(RunOut, "model_validate"):
        run_out = RunOut.model_validate(run)
        task_out = TaskOut.model_validate(task)
        host_out = HostLiteOut.model_validate(host) if host else None
        device_out = DeviceLiteOut.model_validate(device) if device else None
    else:
        run_out = RunOut.from_orm(run)
        task_out = TaskOut.from_orm(task)
        host_out = HostLiteOut.from_orm(host) if host else None
        device_out = DeviceLiteOut.from_orm(device) if device else None

    risk_summary = _load_risk_summary_from_artifacts(run.artifacts)
    summary_metrics = _parse_run_log_summary(run.log_summary)
    alerts = _build_risk_alerts(risk_summary, summary_metrics)
    run_out.risk_summary = risk_summary
    return RunReportOut(
        generated_at=datetime.utcnow(),
        run=run_out,
        task=task_out,
        host=host_out,
        device=device_out,
        summary_metrics=summary_metrics,
        risk_summary=risk_summary,
        alerts=alerts,
    )


def _model_to_dict(payload: Any) -> Dict[str, Any]:
    if hasattr(payload, "model_dump"):
        return payload.model_dump()
    return payload.dict()


def _report_to_markdown(report: RunReportOut) -> str:
    risk = report.risk_summary if isinstance(report.risk_summary, dict) else {}
    counts = risk.get("counts") if isinstance(risk, dict) else {}
    if not isinstance(counts, dict):
        counts = {}
    lines = [
        f"# Run Report - {report.run.id}",
        "",
        f"- Generated: {report.generated_at.isoformat()}",
        f"- Task: {report.task.name} ({report.task.type})",
        f"- Run Status: {report.run.status}",
        f"- Device: {report.device.serial if report.device else 'N/A'}",
        f"- Host: {report.host.name if report.host else 'N/A'}",
        "",
        "## Summary Metrics",
    ]
    if report.summary_metrics:
        for key, value in report.summary_metrics.items():
            lines.append(f"- {key}: {value}")
    else:
        lines.append("- N/A")

    lines.extend(
        [
            "",
            "## Risk Summary",
            f"- risk_level: {risk.get('risk_level', 'UNKNOWN') if isinstance(risk, dict) else 'UNKNOWN'}",
            f"- events_total: {counts.get('events_total', 0)}",
            f"- restart_count: {counts.get('restart_count', 0)}",
            f"- aee_entries: {counts.get('aee_entries', 0)}",
            "",
            "## Alerts",
        ]
    )
    if report.alerts:
        for item in report.alerts:
            lines.append(f"- [{item.severity}] {item.code}: {item.message}")
    else:
        lines.append("- No alerts")

    lines.extend(["", "## Artifacts"])
    if report.run.artifacts:
        for item in report.run.artifacts:
            lines.append(
                f"- id={item.id}, uri={item.storage_uri}, size={item.size_bytes}, checksum={item.checksum}"
            )
    else:
        lines.append("- N/A")
    return "\n".join(lines)


def _default_jira_template() -> Dict[str, Any]:
    return {
        "default": {
            "project_key": REPORT_JIRA_PROJECT_KEY,
            "issue_type": "Bug",
            "component": "Stability-Core",
            "fix_version": None,
            "assignee": None,
            "labels": ["stability"],
            "custom_fields": {},
        },
        "task_type": {},
        "risk_level": {},
    }


def _load_jira_template() -> Dict[str, Any]:
    template = _default_jira_template()
    if not REPORT_JIRA_TEMPLATE_JSON:
        return template
    try:
        payload = json.loads(REPORT_JIRA_TEMPLATE_JSON)
    except Exception:
        logger.warning("invalid_jira_template_json")
        return template
    if not isinstance(payload, dict):
        return template
    for key in ("default", "task_type", "risk_level"):
        block = payload.get(key)
        if isinstance(block, dict):
            template[key] = block
    return template


def _unique_str_list(values: List[Any]) -> List[str]:
    result: List[str] = []
    seen: Set[str] = set()
    for item in values:
        text = str(item).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _resolve_jira_fields(
    task_type: str,
    risk_level: str,
    computed_priority: str,
) -> Dict[str, Any]:
    template = _load_jira_template()
    default_map = template.get("default", {})
    task_map = template.get("task_type", {})
    risk_map = template.get("risk_level", {})

    resolved: Dict[str, Any] = {}
    if isinstance(default_map, dict):
        resolved.update(default_map)

    task_block = task_map.get(task_type.upper()) if isinstance(task_map, dict) else None
    if isinstance(task_block, dict):
        resolved.update(task_block)

    risk_block = risk_map.get(risk_level.upper()) if isinstance(risk_map, dict) else None
    if isinstance(risk_block, dict):
        resolved.update(risk_block)

    merged_labels: List[Any] = []
    for source in (default_map, task_block, risk_block):
        if isinstance(source, dict) and isinstance(source.get("labels"), list):
            merged_labels.extend(source.get("labels", []))
    resolved["labels"] = _unique_str_list(merged_labels)

    custom_fields: Dict[str, Any] = {}
    for source in (default_map, task_block, risk_block):
        if isinstance(source, dict) and isinstance(source.get("custom_fields"), dict):
            custom_fields.update(source["custom_fields"])
    resolved["custom_fields"] = custom_fields

    raw_priority = str(resolved.get("priority", computed_priority)).strip().capitalize()
    if raw_priority not in {"Critical", "Major", "Minor"}:
        raw_priority = computed_priority
    resolved["priority"] = raw_priority
    resolved["project_key"] = str(resolved.get("project_key") or REPORT_JIRA_PROJECT_KEY)
    resolved["issue_type"] = str(resolved.get("issue_type") or "Bug")
    resolved["component"] = (
        str(resolved["component"]).strip() if resolved.get("component") else None
    )
    resolved["fix_version"] = (
        str(resolved["fix_version"]).strip() if resolved.get("fix_version") else None
    )
    resolved["assignee"] = str(resolved["assignee"]).strip() if resolved.get("assignee") else None
    return resolved


def _build_jira_draft(report: RunReportOut) -> JiraDraftOut:
    has_high = any(item.severity == "HIGH" for item in report.alerts)
    has_medium = any(item.severity == "MEDIUM" for item in report.alerts)
    priority = "Minor"
    if has_high:
        priority = "Critical"
    elif has_medium:
        priority = "Major"

    risk_level = "UNKNOWN"
    if isinstance(report.risk_summary, dict):
        risk_level = str(report.risk_summary.get("risk_level", "UNKNOWN")).upper()

    resolved = _resolve_jira_fields(report.task.type, risk_level, priority)
    priority = resolved["priority"]
    issue_type = resolved["issue_type"]
    project_key = resolved["project_key"]
    component = resolved.get("component")
    fix_version = resolved.get("fix_version")
    assignee = resolved.get("assignee")
    custom_fields = resolved.get("custom_fields") or {}
    mapped_labels = resolved.get("labels") or []

    summary = (
        f"[{project_key}] [Stability] {report.task.type} run#{report.run.id} "
        f"{risk_level} on {report.device.serial if report.device else 'UNKNOWN_DEVICE'}"
    )
    alert_lines = (
        [f"- [{item.severity}] {item.code}: {item.message}" for item in report.alerts]
        if report.alerts
        else ["- No alerts generated"]
    )
    artifact_lines = (
        [f"- {item.storage_uri}" for item in report.run.artifacts]
        if report.run.artifacts
        else ["- N/A"]
    )
    summary_lines = (
        [f"- {k}: {v}" for k, v in report.summary_metrics.items()]
        if report.summary_metrics
        else ["- N/A"]
    )

    description = "\n".join(
        [
            "h2. Run Context",
            f"- task_id: {report.task.id}",
            f"- run_id: {report.run.id}",
            f"- task_type: {report.task.type}",
            f"- status: {report.run.status}",
            f"- device: {report.device.serial if report.device else 'N/A'}",
            f"- host: {report.host.name if report.host else 'N/A'}",
            "",
            "h2. Summary Metrics",
            *summary_lines,
            "",
            "h2. Alerts",
            *alert_lines,
            "",
            "h2. Artifacts",
            *artifact_lines,
        ]
    )

    labels = [
        "stability",
        f"task-{report.task.type.lower()}",
        f"risk-{risk_level.lower()}",
        f"run-status-{report.run.status.lower()}",
    ]
    labels.extend(mapped_labels)
    if report.alerts:
        labels.append("auto-alert")
    labels = _unique_str_list(labels)

    return JiraDraftOut(
        run_id=report.run.id,
        task_id=report.task.id,
        project_key=project_key,
        issue_type=issue_type,
        priority=priority,  # type: ignore[arg-type]
        component=component,
        fix_version=fix_version,
        assignee=assignee,
        summary=summary,
        description=description,
        labels=labels,
        environment={
            "host": _model_to_dict(report.host) if report.host else None,
            "device": _model_to_dict(report.device) if report.device else None,
        },
        custom_fields=custom_fields,
        extra={
            "risk_summary": report.risk_summary,
            "summary_metrics": report.summary_metrics,
            "alert_count": len(report.alerts),
            "template_resolved": resolved,
        },
    )


def _ensure_task_transition(task: Task, target_status: TaskStatus) -> None:
    current_status = task.status
    if current_status == target_status:
        return
    allowed = TASK_STATUS_TRANSITIONS.get(current_status, set())
    if target_status not in allowed:
        raise HTTPException(
            status_code=409,
            detail=f"illegal task transition {current_status.value}->{target_status.value}",
        )


def _ensure_run_transition(run: TaskRun, target_status: RunStatus) -> None:
    current_status = run.status
    if current_status == target_status:
        return
    allowed = RUN_STATUS_TRANSITIONS.get(current_status, set())
    if target_status not in allowed:
        raise HTTPException(
            status_code=409,
            detail=f"illegal run transition {current_status.value}->{target_status.value}",
        )


def _acquire_device_lock(db: Session, device_id: int, run_id: int) -> None:
    now = datetime.utcnow()
    expires_at = now + timedelta(seconds=DEVICE_LOCK_LEASE_SECONDS)
    result = db.execute(
        text(
            """
            UPDATE devices
            SET status = :busy_status,
                lock_run_id = :run_id,
                lock_expires_at = :expires_at
            WHERE id = :device_id
              AND status = :online_status
              AND (lock_run_id IS NULL OR lock_expires_at IS NULL OR lock_expires_at < :now)
            """
        ),
        {
            "device_id": device_id,
            "run_id": run_id,
            "expires_at": expires_at,
            "now": now,
            "busy_status": DeviceStatus.BUSY.value,
            "online_status": DeviceStatus.ONLINE.value,
        },
    )
    if result.rowcount != 1:
        raise HTTPException(status_code=409, detail="device busy")


def _extend_device_lock(db: Session, device_id: int, run_id: int) -> None:
    now = datetime.utcnow()
    expires_at = now + timedelta(seconds=DEVICE_LOCK_LEASE_SECONDS)
    db.execute(
        text(
            """
            UPDATE devices
            SET lock_expires_at = :expires_at
            WHERE id = :device_id AND lock_run_id = :run_id
            """
        ),
        {
            "device_id": device_id,
            "run_id": run_id,
            "expires_at": expires_at,
        },
    )


def _release_device_lock(db: Session, device_id: int, run_id: int) -> None:
    db.execute(
        text(
            """
            UPDATE devices
            SET status = CASE
                    WHEN status = :busy_status THEN :online_status
                    ELSE status
                END,
                lock_run_id = NULL,
                lock_expires_at = NULL
            WHERE id = :device_id AND lock_run_id = :run_id
            """
        ),
        {
            "device_id": device_id,
            "run_id": run_id,
            "busy_status": DeviceStatus.BUSY.value,
            "online_status": DeviceStatus.ONLINE.value,
        },
    )


@router.get("/tasks", response_model=List[TaskOut])
def list_tasks(db: Session = Depends(get_db)):
    """获取任务列表"""
    return db.query(Task).order_by(Task.id.desc()).all()


@router.get("/task-templates", response_model=List[TaskTemplateOut])
def list_task_templates():
    templates = list_core_templates()
    return [
        TaskTemplateOut(
            type=tpl.type,
            name=tpl.name,
            description=tpl.description,
            default_params=tpl.default_params,
            script_paths=tpl.script_paths,
        )
        for tpl in templates
    ]


@router.post("/tasks", response_model=TaskOut)
def create_task(payload: TaskCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    resolved_target_device_id = payload.target_device_id
    if resolved_target_device_id is None and payload.device_serial:
        device = db.query(Device).filter(Device.serial == payload.device_serial).first()
        if not device:
            raise HTTPException(status_code=400, detail="target device serial not found")
        resolved_target_device_id = device.id

    if resolved_target_device_id is not None:
        target_device = db.get(Device, resolved_target_device_id)
        if not target_device:
            raise HTTPException(status_code=400, detail="target device not found")
        if target_device.status != DeviceStatus.ONLINE:
            raise HTTPException(status_code=409, detail="target device is not online")
        if not target_device.host_id:
            raise HTTPException(status_code=409, detail="target device has no host binding")
        host = db.get(Host, target_device.host_id)
        if not host or host.status != HostStatus.ONLINE:
            raise HTTPException(status_code=409, detail="target device host is not online")

    task = Task(
        name=payload.name,
        type=payload.type,
        template_id=payload.template_id,
        params=payload.params,
        target_device_id=resolved_target_device_id,
        status=TaskStatus.PENDING,
        priority=payload.priority,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    logger.info(
        "task_created",
        extra={
            "task_id": task.id,
            "type": task.type,
            "target_device_id": task.target_device_id,
            "priority": task.priority,
        },
    )
    return task


@router.get("/tasks/{task_id}", response_model=TaskOut)
def get_task(task_id: int, db: Session = Depends(get_db)):
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    return task


@router.get("/tasks/{task_id}/runs", response_model=List[RunOut])
def get_task_runs(task_id: int, db: Session = Depends(get_db)):
    """获取任务的所有运行记录"""
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    runs = (
        db.query(TaskRun)
        .options(selectinload(TaskRun.artifacts))
        .filter(TaskRun.task_id == task_id)
        .order_by(TaskRun.id.desc())
        .all()
    )
    run_items: List[RunOut] = []
    for run in runs:
        if hasattr(RunOut, "model_validate"):
            run_out = RunOut.model_validate(run)
        else:
            run_out = RunOut.from_orm(run)
        run_out.risk_summary = _load_risk_summary_from_artifacts(run.artifacts)
        run_items.append(run_out)
    return run_items


@router.get("/runs/{run_id}/report", response_model=RunReportOut)
def get_run_report(run_id: int, db: Session = Depends(get_db)):
    return _compose_run_report(db, run_id)


@router.get("/runs/{run_id}/report/export")
def export_run_report(run_id: int, format: str = Query("markdown"), db: Session = Depends(get_db)):
    report = _compose_run_report(db, run_id)
    fmt = format.strip().lower()
    if fmt == "json":
        return JSONResponse(content=jsonable_encoder(_model_to_dict(report)))
    if fmt != "markdown":
        raise HTTPException(status_code=400, detail="format must be markdown or json")
    markdown = _report_to_markdown(report)
    return PlainTextResponse(
        markdown,
        headers={"Content-Disposition": f'attachment; filename="run-{run_id}-report.md"'},
    )


@router.post("/runs/{run_id}/jira-draft", response_model=JiraDraftOut)
def create_run_jira_draft(run_id: int, db: Session = Depends(get_db)):
    report = _compose_run_report(db, run_id)
    return _build_jira_draft(report)


@router.get("/tasks/{task_id}/runs/{run_id}/artifacts/{artifact_id}/download")
def download_run_artifact(task_id: int, run_id: int, artifact_id: int, db: Session = Depends(get_db)):
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")

    run = db.get(TaskRun, run_id)
    if not run or run.task_id != task_id:
        raise HTTPException(status_code=404, detail="run not found")

    artifact = db.get(LogArtifact, artifact_id)
    if not artifact or artifact.run_id != run_id:
        raise HTTPException(status_code=404, detail="artifact not found")

    target = _artifact_download_target(artifact.storage_uri)
    if target["kind"] == "redirect":
        return RedirectResponse(url=target["url"], status_code=307)

    local_path = Path(target["path"])
    media_type = "application/gzip" if local_path.suffixes[-2:] == [".tar", ".gz"] else None
    return FileResponse(path=str(local_path), filename=local_path.name, media_type=media_type)


@router.post("/tasks/{task_id}/dispatch", response_model=RunOut)
def dispatch_task(task_id: int, payload: TaskDispatch, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    host = db.get(Host, payload.host_id)
    device = db.get(Device, payload.device_id)
    if not host or not device:
        raise HTTPException(status_code=400, detail="host or device not found")

    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    if task.target_device_id and task.target_device_id != payload.device_id:
        raise HTTPException(status_code=409, detail="task target device mismatch")

    # Atomic status update to prevent concurrent dispatch
    updated = db.execute(
        text(
            "UPDATE tasks SET status = :queued WHERE id = :task_id AND status = :pending"
        ),
        {
            "task_id": task.id,
            "queued": TaskStatus.QUEUED.value,
            "pending": TaskStatus.PENDING.value,
        },
    )
    if updated.rowcount != 1:
        raise HTTPException(status_code=409, detail="task not pending")

    run = TaskRun(
        task_id=task.id,
        host_id=host.id,
        device_id=device.id,
        status=RunStatus.QUEUED,
    )
    db.add(run)
    db.flush()
    _acquire_device_lock(db, device.id, run.id)
    db.commit()
    db.refresh(run)
    return run


@router.get("/agent/runs/pending", response_model=List[RunAgentOut])
def agent_pending_runs(
    host_id: int = Query(...),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    _: bool = Depends(verify_agent_secret),
):
    runs = (
        db.query(TaskRun)
        .filter(TaskRun.host_id == host_id, TaskRun.status == RunStatus.QUEUED)
        .order_by(TaskRun.id)
        .limit(limit)
        .all()
    )
    for run in runs:
        _ensure_run_transition(run, RunStatus.DISPATCHED)
        run.status = RunStatus.DISPATCHED
    db.commit()
    payload = []
    for run in runs:
        task = db.get(Task, run.task_id)
        device = db.get(Device, run.device_id)
        payload.append(
            RunAgentOut(
                id=run.id,
                task_id=run.task_id,
                host_id=run.host_id,
                device_id=run.device_id,
                device_serial=device.serial if device else None,
                task_type=task.type if task else "",
                task_params=task.params if task else {},
            )
        )
    return payload


@router.post("/agent/runs/{run_id}/heartbeat")
def agent_run_heartbeat(run_id: int, payload: RunUpdate, db: Session = Depends(get_db), _: bool = Depends(verify_agent_secret)):
    run = db.get(TaskRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="run not found")
    normalized_status = _normalize_run_status(payload.status)
    if normalized_status:
        try:
            target_status = RunStatus(normalized_status)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid run status")
        _ensure_run_transition(run, target_status)
        run.status = target_status
    if payload.started_at:
        run.started_at = payload.started_at
    if payload.finished_at:
        run.finished_at = payload.finished_at
    if payload.exit_code is not None:
        run.exit_code = payload.exit_code
    if payload.error_code:
        run.error_code = payload.error_code
    if payload.error_message:
        run.error_message = payload.error_message
    if payload.log_summary:
        run.log_summary = payload.log_summary
    run.last_heartbeat_at = datetime.utcnow()
    if run.status == RunStatus.RUNNING:
        if run.started_at is None:
            run.started_at = datetime.utcnow()
        task = db.get(Task, run.task_id)
        if task:
            _ensure_task_transition(task, TaskStatus.RUNNING)
            task.status = TaskStatus.RUNNING
        _extend_device_lock(db, run.device_id, run.id)
    db.commit()

    # Broadcast log lines to WebSocket if provided
    if payload.log_lines:
        try:
            # Lazy import to avoid circular dependency
            from backend.api.routes.websocket import manager
            # Get device serial for log context
            device = db.get(Device, run.device_id)
            device_serial = device.serial if device else "unknown"
            timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

            # Broadcast each log line
            for line in payload.log_lines:
                asyncio.create_task(
                    manager.broadcast(
                        f"/ws/logs/{run_id}",
                        {
                            "type": "LOG",
                            "payload": {
                                "timestamp": timestamp,
                                "level": "INFO",
                                "device": device_serial,
                                "message": line,
                            },
                        },
                    )
                )

            # Also broadcast progress if provided
            if payload.progress is not None:
                asyncio.create_task(
                    manager.broadcast(
                        f"/ws/logs/{run_id}",
                        {
                            "type": "PROGRESS",
                            "payload": {"progress": payload.progress},
                        },
                    )
                )
        except ImportError:
            logger.warning("websocket_manager_not_available")
        except Exception as e:
            logger.warning(f"log_broadcast_failed: {e}")

    return {"ok": True}


@router.post("/agent/runs/{run_id}/complete")
def agent_run_complete(
    run_id: int,
    payload: Dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
    _: bool = Depends(verify_agent_secret),
):
    parsed_payload: RunCompleteIn
    if "update" in payload:
        # New contract
        try:
            parsed_payload = RunCompleteIn(**payload)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"invalid complete payload: {exc}")
    else:
        # Legacy contract compatibility layer
        logger.warning("legacy_run_complete_payload", extra={"run_id": run_id})
        legacy_status = _normalize_run_status(payload.get("status"))
        if legacy_status is None:
            raise HTTPException(status_code=400, detail="status required")

        legacy_update = RunUpdate(
            status=legacy_status,
            error_code=payload.get("error_code"),
            error_message=payload.get("error_message"),
            exit_code=payload.get("exit_code"),
            started_at=payload.get("started_at"),
            finished_at=payload.get("finished_at"),
            log_summary=payload.get("log_summary"),
        )
        artifact = payload.get("artifact")
        try:
            parsed_payload = RunCompleteIn(
                update=legacy_update,
                artifact=LogArtifactIn(**artifact) if isinstance(artifact, dict) else None,
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"invalid legacy complete payload: {exc}")

    run = db.get(TaskRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="run not found")
    if not parsed_payload.update.status:
        raise HTTPException(status_code=400, detail="status required")
    try:
        target_status = RunStatus(_normalize_run_status(parsed_payload.update.status))
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid run status")
    _ensure_run_transition(run, target_status)
    run.status = target_status
    run.finished_at = parsed_payload.update.finished_at or datetime.utcnow()
    run.exit_code = parsed_payload.update.exit_code
    run.error_code = parsed_payload.update.error_code
    run.error_message = parsed_payload.update.error_message
    run.log_summary = parsed_payload.update.log_summary
    if parsed_payload.artifact:
        db.add(
            LogArtifact(
                run_id=run.id,
                storage_uri=parsed_payload.artifact.storage_uri,
                size_bytes=parsed_payload.artifact.size_bytes,
                checksum=parsed_payload.artifact.checksum,
            )
        )
    task = db.get(Task, run.task_id)
    if task:
        if run.status == RunStatus.FINISHED:
            _ensure_task_transition(task, TaskStatus.COMPLETED)
            task.status = TaskStatus.COMPLETED
        elif run.status == RunStatus.FAILED:
            _ensure_task_transition(task, TaskStatus.FAILED)
            task.status = TaskStatus.FAILED
        elif run.status == RunStatus.CANCELED:
            _ensure_task_transition(task, TaskStatus.CANCELED)
            task.status = TaskStatus.CANCELED
    _release_device_lock(db, run.device_id, run.id)
    db.commit()
    logger.info(
        "run_completed",
        extra={
            "run_id": run.id,
            "task_id": run.task_id,
            "host_id": run.host_id,
            "device_id": run.device_id,
            "run_status": run.status.value,
        },
    )
    return {"ok": True}


@router.post("/agent/runs/{run_id}/extend_lock")
def extend_device_lock(
    run_id: int,
    db: Session = Depends(get_db),
    _: bool = Depends(verify_agent_secret),
):
    """
    延长设备锁租期
    由 Agent 定期调用以维持锁，防止长任务执行时锁过期
    """
    now = datetime.utcnow()

    # 查询运行记录
    run = db.get(TaskRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="run not found")

    if run.status != RunStatus.RUNNING:
        raise HTTPException(
            status_code=409,
            detail=f"run is not running, current status: {run.status.value}",
        )

    # 验证设备锁
    device = db.get(Device, run.device_id)
    if not device:
        raise HTTPException(status_code=404, detail="device not found")

    if device.lock_run_id != run_id:
        raise HTTPException(
            status_code=409,
            detail="lock lost, device may be reassigned",
        )

    # 计算新的过期时间
    new_expires = now + timedelta(seconds=DEVICE_LOCK_LEASE_SECONDS)

    # 更新锁过期时间和心跳时间
    device.lock_expires_at = new_expires
    device.last_heartbeat = now
    run.last_heartbeat_at = now

    db.commit()

    logger.info(
        "lock_extended",
        extra={
            "run_id": run_id,
            "device_id": device.id,
            "expires_at": new_expires.isoformat(),
        },
    )

    return {
        "status": "ok",
        "run_id": run_id,
        "device_id": device.id,
        "expires_at": new_expires.isoformat(),
        "extended_at": now.isoformat(),
    }


@router.post("/agent/logs", response_model=AgentLogOut)
def query_agent_logs(query: AgentLogQuery, db: Session = Depends(get_db), _: bool = Depends(verify_agent_secret)):
    """通过SSH查询Linux host上的agent日志"""
    host = db.get(Host, query.host_id)
    if not host:
        raise HTTPException(status_code=404, detail="host not found")

    # Validate log_path to prevent command injection
    # Only allow alphanumeric, dash, underscore, dot, slash, and space
    import re
    if not re.match(r'^[a-zA-Z0-9_./\-\s]+$', query.log_path):
        raise HTTPException(status_code=400, detail="Invalid log_path: contains disallowed characters")

    # 构建SSH连接参数
    ssh_host = host.ip
    ssh_port = host.ssh_port or 22
    ssh_user = host.ssh_user or "root"
    ssh_password = host.extra.get("ssh_password") if host.extra else None
    ssh_key_path = host.extra.get("ssh_key_path") if host.extra else None

    try:
        # 建立SSH连接
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        connect_kwargs = {
            "hostname": ssh_host,
            "port": ssh_port,
            "username": ssh_user,
            "timeout": 10,
        }

        if ssh_key_path and os.path.exists(ssh_key_path):
            connect_kwargs["key_filename"] = ssh_key_path
        elif ssh_password:
            connect_kwargs["password"] = ssh_password
        else:
            # 尝试使用默认密钥
            pass

        client.connect(**connect_kwargs)

        # 执行命令读取日志
        cmd = f"tail -n {query.lines} {shlex.quote(query.log_path)} 2>/dev/null || echo 'LOG_FILE_NOT_FOUND'"
        stdin, stdout, stderr = client.exec_command(cmd)
        content = stdout.read().decode("utf-8", errors="replace")
        error = stderr.read().decode("utf-8", errors="replace")

        client.close()

        if content.strip() == "LOG_FILE_NOT_FOUND":
            return AgentLogOut(
                host_id=query.host_id,
                log_path=query.log_path,
                content="",
                lines_read=0,
                error=f"Log file not found: {query.log_path}",
            )

        lines_read = len([l for l in content.split("\n") if l.strip()])

        return AgentLogOut(
            host_id=query.host_id,
            log_path=query.log_path,
            content=content,
            lines_read=lines_read,
            error=error if error else None,
        )

    except paramiko.AuthenticationException:
        return AgentLogOut(
            host_id=query.host_id,
            log_path=query.log_path,
            content="",
            lines_read=0,
            error="SSH authentication failed. Please check ssh_user and ssh_password/ssh_key.",
        )
    except paramiko.SSHException as e:
        return AgentLogOut(
            host_id=query.host_id,
            log_path=query.log_path,
            content="",
            lines_read=0,
            error=f"SSH connection error: {str(e)}",
        )
    except Exception as e:
        return AgentLogOut(
            host_id=query.host_id,
            log_path=query.log_path,
            content="",
            lines_read=0,
            error=f"Failed to query agent logs: {str(e)}",
        )


@router.post("/tasks/{task_id}/cancel", response_model=RunOut)
def cancel_task(task_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    """
    Cancel a pending or queued task.
    If the task is already running, it will be marked for cancellation.
    """
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")

    # Can only cancel tasks that are pending, queued, or running
    if task.status not in (TaskStatus.PENDING, TaskStatus.QUEUED, TaskStatus.RUNNING):
        raise HTTPException(
            status_code=409,
            detail=f"cannot cancel task in status {task.status.value}"
        )

    # Get the latest run for this task
    run = (
        db.query(TaskRun)
        .filter(TaskRun.task_id == task_id)
        .order_by(TaskRun.id.desc())
        .first()
    )

    # Update task status
    _ensure_task_transition(task, TaskStatus.CANCELED)
    task.status = TaskStatus.CANCELED

    # Update run status if exists and not already finished
    if run and run.status in (RunStatus.QUEUED, RunStatus.DISPATCHED, RunStatus.RUNNING):
        run.status = RunStatus.CANCELED
        run.finished_at = datetime.utcnow()
        # Release device lock if held
        if run.device_id:
            _release_device_lock(db, run.device_id, run.id)

    db.commit()
    db.refresh(run if run else task)
    if run:
        return run
    # Return a synthetic RunOut for tasks without runs
    return RunOut(
        id=0,
        task_id=task.id,
        host_id=0,
        device_id=task.target_device_id or 0,
        status=RunStatus.CANCELED,
        started_at=None,
        finished_at=datetime.utcnow(),
        exit_code=None,
        error_message="Task canceled by user",
    )


@router.post("/tasks/{task_id}/retry", response_model=TaskOut)
def retry_task(task_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    """
    Retry a failed or canceled task.
    Resets task to PENDING status - dispatcher will create new run when assigning host/device.
    """
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")

    # Can only retry failed or canceled tasks
    if task.status not in (TaskStatus.FAILED, TaskStatus.CANCELED):
        raise HTTPException(
            status_code=409,
            detail=f"cannot retry task in status {task.status.value}"
        )

    # Check if target device is available (if specified)
    if task.target_device_id:
        device = db.get(Device, task.target_device_id)
        if not device or device.status != DeviceStatus.ONLINE:
            raise HTTPException(
                status_code=409,
                detail="target device is not available"
            )

    # Reset task status to pending - dispatcher will handle creating the run
    task.status = TaskStatus.PENDING
    db.commit()
    db.refresh(task)
    return task
