# -*- coding: utf-8 -*-
"""
报告与 JIRA Draft 生成服务

纯业务逻辑，不依赖 FastAPI HTTPException。
可在 API 路由（runs.py / orchestration.py）和后台线程中安全复用。
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from sqlalchemy.orm import Session
from sqlalchemy.exc import ProgrammingError
from sqlalchemy import text, select
from backend.models.job import JobInstance, JobLogSignal, StepTrace
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun
from backend.models.host import Device, Host
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

_JOB_STATUS_TO_RUN_STATUS = {
    "PENDING": "QUEUED",
    "RUNNING": "RUNNING",
    "COMPLETED": "FINISHED",
    "FAILED": "FAILED",
    "ABORTED": "CANCELED",
    "UNKNOWN": "FAILED",
}

_WF_STATUS_TO_TASK_STATUS = {
    "RUNNING": "RUNNING",
    "SUCCESS": "COMPLETED",
    "PARTIAL_SUCCESS": "COMPLETED",
    "FAILED": "FAILED",
    "DEGRADED": "FAILED",
}

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

_RISK_RATING_RULES: list[dict[str, Any]] = [
    {"keyword": "swt",             "operator": ">=", "threshold": 1, "level": "S"},
    {"keyword": "fatal ne",        "operator": ">=", "threshold": 1, "level": "S"},
    {"keyword": "fatal je",        "operator": ">=", "threshold": 1, "level": "S"},
    {"keyword": "hwt",             "operator": ">=", "threshold": 1, "level": "S"},
    {"keyword": "kernel (ke)",     "operator": ">=", "threshold": 1, "level": "S"},
    {"keyword": "hardware reboot", "operator": ">=", "threshold": 1, "level": "S"},
    {"keyword": "hang",            "operator": ">=", "threshold": 1, "level": "S"},
    {"keyword": "anr",             "operator": ">=", "threshold": 10, "level": "A"},
    {"keyword": "anr",             "operator": ">=", "threshold": 1,  "level": "B"},
    {"keyword": "java",            "operator": ">=", "threshold": 3,  "level": "A"},
    {"keyword": "java",            "operator": ">=", "threshold": 1,  "level": "B"},
    {"keyword": "je",              "operator": ">=", "threshold": 3,  "level": "A"},
    {"keyword": "je",              "operator": ">=", "threshold": 1,  "level": "B"},
    {"keyword": "native",          "operator": ">=", "threshold": 2,  "level": "A"},
    {"keyword": "native",          "operator": ">=", "threshold": 1,  "level": "B"},
    {"keyword": "ne",              "operator": ">=", "threshold": 2,  "level": "A"},
    {"keyword": "ne",              "operator": ">=", "threshold": 1,  "level": "B"},
    {"keyword": "kernel api dump", "operator": ">=", "threshold": 1,  "level": "B"},
]

_RISK_SEVERITY_ORDER = {"S": 3, "A": 2, "B": 1}
_DEFAULT_RISK_LEVEL = "B"


def _classify_subtype(subtype: str, count: int) -> str:
    lowered = subtype.lower()
    matched: dict[str, str] = {}
    for rule in _RISK_RATING_RULES:
        kw = rule["keyword"]
        if kw in lowered and kw not in matched:
            if count >= rule["threshold"]:
                matched[kw] = rule["level"]
    worst = _DEFAULT_RISK_LEVEL
    for level in matched.values():
        if _RISK_SEVERITY_ORDER.get(level, 0) > _RISK_SEVERITY_ORDER.get(worst, 0):
            worst = level
    return worst


def aggregate_risk_summary_from_signals(
    db: Session, job_ids: list[int]
) -> Optional[Dict[str, Any]]:
    if not job_ids:
        return None

    sql = text("""
        SELECT
            COALESCE(extra->>'event_subtype', category) AS subtype,
            COUNT(DISTINCT extra->>'nfs_path') AS dedup_count,
            COUNT(*) AS raw_count
        FROM job_log_signal
        WHERE job_id = ANY(:job_ids)
          AND category IN ('AEE', 'VENDOR_AEE', 'ANR')
        GROUP BY subtype
    """)

    rows = db.execute(sql, {"job_ids": list(job_ids)}).all()

    if not rows:
        return None

    by_type: Dict[str, int] = {}
    by_severity: Dict[str, int] = {"S": 0, "A": 0, "B": 0}
    events_total = 0
    aee_entries = 0
    worst_level = _DEFAULT_RISK_LEVEL

    for subtype, dedup_count, raw_count in rows:
        count = int(dedup_count)
        by_type[subtype] = count
        events_total += count
        if subtype.upper() in ("AEE", "VENDOR_AEE"):
            aee_entries += count
        level = _classify_subtype(subtype, count)
        by_severity[level] = by_severity.get(level, 0) + 1
        if _RISK_SEVERITY_ORDER.get(level, 0) > _RISK_SEVERITY_ORDER.get(worst_level, 0):
            worst_level = level

    return {
        "risk_level": worst_level,
        "counts": {
            "by_type": by_type,
            "by_severity": by_severity,
            "events_total": events_total,
            "aee_entries": aee_entries,
        },
    }


def _model_to_dict(payload: Any) -> Dict[str, Any]:
    return payload.model_dump()


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


def _safe_json_loads(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        payload = json.loads(raw)
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _extract_job_completion_snapshot(db: Session, job_id: int) -> Dict[str, Any]:
    snapshot_trace = (
        db.query(StepTrace)
        .filter(
            StepTrace.job_id == job_id,
            StepTrace.step_id == "__job__",
            StepTrace.event_type == "RUN_COMPLETE",
        )
        .order_by(StepTrace.id.desc())
        .first()
    )
    if not snapshot_trace:
        return {}
    return _safe_json_loads(snapshot_trace.output)


def _compose_job_report(db: Session, job: JobInstance) -> Optional[RunReportOut]:
    """ADR-0020: compose a report from PlanRun + Plan context."""
    plan_run = db.get(PlanRun, job.plan_run_id) if job.plan_run_id else None
    plan = db.get(Plan, job.plan_id) if job.plan_id else None

    host = db.get(Host, str(job.host_id)) if job.host_id is not None else None
    device = db.get(Device, job.device_id)

    snapshot = _extract_job_completion_snapshot(db, job.id)
    update = snapshot.get("update") if isinstance(snapshot.get("update"), dict) else {}
    artifact_payload = snapshot.get("artifact") if isinstance(snapshot.get("artifact"), dict) else None

    artifacts_out = []
    if artifact_payload and artifact_payload.get("storage_uri"):
        artifacts_out.append(
            {
                "id": 0,
                "run_id": job.id,
                "storage_uri": str(artifact_payload.get("storage_uri")),
                "size_bytes": artifact_payload.get("size_bytes"),
                "checksum": artifact_payload.get("checksum"),
                "created_at": job.ended_at or datetime.now(timezone.utc),
            }
        )

    log_summary = update.get("log_summary")
    if not isinstance(log_summary, str):
        log_summary = None

    sibling_job_ids: list[int] = []
    if job.plan_run_id:
        sibling_rows = db.execute(
            select(JobInstance.id).where(JobInstance.plan_run_id == job.plan_run_id)
        ).all()
        sibling_job_ids = [r[0] for r in sibling_rows]
    risk_summary = aggregate_risk_summary_from_signals(db, sibling_job_ids) if sibling_job_ids else None
    summary_metrics = parse_run_log_summary(log_summary)
    alerts = build_risk_alerts(risk_summary, summary_metrics)

    host_id_int = 0
    if job.host_id is not None:
        try:
            host_id_int = int(str(job.host_id))
        except (TypeError, ValueError):
            host_id_int = 0

    task_status = _WF_STATUS_TO_TASK_STATUS.get(
        str(plan_run.status).upper(), "PENDING") if plan_run else "PENDING"
    task_out = TaskOut(
        id=plan.id if plan else 0,
        name=plan.name if plan else "unknown",
        type="PLAN",
        template_id=None,
        tool_id=None,
        params={},
        tool_snapshot=None,
        target_device_id=job.device_id,
        status=task_status,
        priority=0,
        group_id=None,
        is_distributed=False,
        runs_count=None,
        pipeline_def=job.pipeline_def,
        created_at=plan.created_at if plan else (job.created_at or datetime.now(timezone.utc)),
    )

    run_status = _JOB_STATUS_TO_RUN_STATUS.get(str(job.status).upper(), str(job.status))
    run_out = RunOut(
        id=job.id,
        task_id=plan.id if plan else 0,
        host_id=host_id_int,
        device_id=job.device_id,
        status=run_status,
        group_id=None,
        progress=100 if run_status in {"FINISHED", "FAILED", "CANCELED"} else 0,
        progress_message=None,
        started_at=job.started_at,
        finished_at=job.ended_at,
        exit_code=update.get("exit_code"),
        error_code=update.get("error_code"),
        error_message=update.get("error_message") or job.status_reason,
        log_summary=log_summary,
        artifacts=artifacts_out,
        risk_summary=risk_summary,
    )

    host_out = HostLiteOut.model_validate(host) if host else None
    device_out = DeviceLiteOut.model_validate(device) if device else None
    return RunReportOut(
        generated_at=datetime.now(timezone.utc),
        run=run_out,
        task=task_out,
        host=host_out,
        device=device_out,
        summary_metrics=summary_metrics,
        risk_summary=risk_summary,
        alerts=alerts,
    )


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
    if risk_level == "S":
        alerts.append(
            RiskAlertOut(
                code="RISK_LEVEL_CRITICAL",
                severity="HIGH",
                message="Risk level is S (Critical)",
                metric="risk_level",
            )
        )
    elif risk_level == "A":
        alerts.append(
            RiskAlertOut(
                code="RISK_LEVEL_HIGH",
                severity="HIGH",
                message="Risk level is A (High)",
                metric="risk_level",
            )
        )

    by_severity = counts.get("by_severity") if isinstance(counts, dict) else None
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

    crash_count = 0
    if isinstance(by_severity, dict):
        crash_count = int(by_severity.get("S", 0) or 0)
    crash_count = max(crash_count, int(by_type.get("CRASH", 0) or 0))
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
    Build a RunReportOut for the given run_id (JobInstance path only).

    Returns None if the run is not found (instead of raising
    HTTPException), making it safe for use outside request context.
    """
    try:
        job = db.get(JobInstance, run_id)
    except ProgrammingError:
        db.rollback()
        job = None
    if job:
        return _compose_job_report(db, job)
    return None


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


# ---------------------------------------------------------------------------
# Public API: PlanRun-level aggregate summary
# ---------------------------------------------------------------------------

def compose_plan_run_summary(db: Session, run_id: int) -> Optional[Dict[str, Any]]:
    """ADR-0020: Build an aggregate summary for a PlanRun across all its jobs.

    Returns a dict with status matrix, failure distribution, pass rate,
    and per-device breakdown.  Returns None if the run is not found.
    """
    run = db.get(PlanRun, run_id)
    if run is None:
        return None

    definition = db.get(Plan, run.plan_id)

    jobs: List[JobInstance] = (
        db.query(JobInstance)
        .filter(JobInstance.plan_run_id == run_id)
        .all()
    )

    device_ids = {j.device_id for j in jobs}
    devices_by_id: Dict[int, Device] = {}
    if device_ids:
        rows = db.query(Device).filter(Device.id.in_(device_ids)).all()
        devices_by_id = {d.id: d for d in rows}

    status_counts: Dict[str, int] = {}
    device_results: List[Dict[str, Any]] = []
    total_duration_seconds = 0.0

    for job in jobs:
        s = job.status or "UNKNOWN"
        status_counts[s] = status_counts.get(s, 0) + 1

        dev = devices_by_id.get(job.device_id)
        duration = None
        if job.started_at and job.ended_at:
            duration = (job.ended_at - job.started_at).total_seconds()
            total_duration_seconds += duration

        device_results.append({
            "job_id": job.id,
            "device_id": job.device_id,
            "device_serial": dev.serial if dev else None,
            "status": s,
            "status_reason": job.status_reason,
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "ended_at": job.ended_at.isoformat() if job.ended_at else None,
            "duration_seconds": duration,
        })

    total = len(jobs)
    completed = status_counts.get("COMPLETED", 0)
    failed = status_counts.get("FAILED", 0) + status_counts.get("ABORTED", 0)
    pass_rate = (completed / total * 100) if total > 0 else 0.0

    return {
        "plan_run_id": run.id,
        "plan_id": run.plan_id,
        "plan_name": definition.name if definition else None,
        "status": run.status,
        "failure_threshold": run.failure_threshold,
        "triggered_by": run.triggered_by,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "ended_at": run.ended_at.isoformat() if run.ended_at else None,
        "result_summary": run.result_summary,
        "statistics": {
            "total_jobs": total,
            "status_distribution": status_counts,
            "pass_rate": round(pass_rate, 2),
            "failed_count": failed,
            "avg_duration_seconds": round(total_duration_seconds / total, 1) if total > 0 else 0,
        },
        "device_results": device_results,
    }
