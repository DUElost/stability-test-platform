# -*- coding: utf-8 -*-
"""
报告与 JIRA Draft 生成服务

从 backend/api/routes/tasks.py 提取的纯业务逻辑，不依赖 FastAPI HTTPException。
可在 API 路由和后台线程中安全复用。
"""

import json
import logging
import os
import tarfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from urllib.parse import unquote, urlparse

from sqlalchemy.orm import Session, selectinload

from backend.models.schemas import Device, Host, LogArtifact, Task, TaskRun
from backend.api.schemas import (
    DeviceLiteOut,
    HostLiteOut,
    JiraDraftOut,
    RiskAlertOut,
    RunOut,
    RunReportOut,
    TaskOut,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration (mirrors tasks.py env vars)
# ---------------------------------------------------------------------------
REPORT_ALERT_ANR_THRESHOLD = int(os.getenv("RUN_REPORT_ALERT_ANR_THRESHOLD", "1"))
REPORT_ALERT_CRASH_THRESHOLD = int(os.getenv("RUN_REPORT_ALERT_CRASH_THRESHOLD", "1"))
REPORT_ALERT_RESTART_THRESHOLD = int(os.getenv("RUN_REPORT_ALERT_RESTART_THRESHOLD", "2"))
REPORT_JIRA_PROJECT_KEY = os.getenv("RUN_REPORT_JIRA_PROJECT_KEY", "STABILITY")
REPORT_JIRA_TEMPLATE_JSON = os.getenv("RUN_REPORT_JIRA_TEMPLATE_JSON", "").strip()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _model_to_dict(payload: Any) -> Dict[str, Any]:
    if hasattr(payload, "model_dump"):
        return payload.model_dump()
    return payload.dict()


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
        if local_path is None:
            continue
        if not local_path.exists():
            continue

        if local_path.suffix == ".json":
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


# ---------------------------------------------------------------------------
# Public API: report composition
# ---------------------------------------------------------------------------

def parse_run_log_summary(log_summary: Optional[str]) -> Dict[str, Any]:
    """Parse semicolon-delimited key=value log summary into a dict."""
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


def build_risk_alerts(
    risk_summary: Optional[Dict[str, Any]],
    summary_metrics: Dict[str, Any],
) -> List[RiskAlertOut]:
    """Generate risk alerts from risk summary and summary metrics."""
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


def compose_run_report(db: Session, run_id: int) -> Optional[RunReportOut]:
    """
    Build a RunReportOut for the given run_id.

    Returns None if the run or its task is not found (instead of raising
    HTTPException), making it safe for use outside request context.
    """
    run = (
        db.query(TaskRun)
        .options(selectinload(TaskRun.artifacts))
        .filter(TaskRun.id == run_id)
        .first()
    )
    if not run:
        return None

    task = db.get(Task, run.task_id)
    if not task:
        return None

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
    summary_metrics = parse_run_log_summary(run.log_summary)
    alerts = build_risk_alerts(risk_summary, summary_metrics)
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


# ---------------------------------------------------------------------------
# Public API: JIRA draft
# ---------------------------------------------------------------------------

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


def build_jira_draft(report: RunReportOut) -> JiraDraftOut:
    """Build a JIRA draft from a completed run report.  Stateless & DB-free."""
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
