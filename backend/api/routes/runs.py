"""Job run endpoints: report, JIRA draft, steps, artifacts.

Extracted from tasks.py (Wave 8) — these are independent endpoints
that operate on JobInstance records, not part of the legacy compatibility layer.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import List
from urllib.parse import unquote, urlparse

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse
from sqlalchemy.orm import Session

from backend.api.schemas import JiraDraftOut, RunReportOut, RunStepOut
from backend.api.routes.auth import get_current_active_user, User
from backend.core.database import get_db
from backend.services.report_service import compose_run_report, build_jira_draft, _model_to_dict

router = APIRouter(prefix="/api/v1", tags=["runs"])
logger = logging.getLogger(__name__)


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

    lines.extend([
        "",
        "## Risk Summary",
        f"- risk_level: {risk.get('risk_level', 'UNKNOWN') if isinstance(risk, dict) else 'UNKNOWN'}",
        f"- events_total: {counts.get('events_total', 0)}",
        f"- restart_count: {counts.get('restart_count', 0)}",
        f"- aee_entries: {counts.get('aee_entries', 0)}",
        "",
        "## Alerts",
    ])
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


def _artifact_download_target(storage_uri: str) -> dict[str, str]:
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


# ── Report ────────────────────────────────────────────────────────────────────


@router.get("/runs/{run_id}/report", response_model=RunReportOut)
def get_run_report(run_id: int, db: Session = Depends(get_db)):
    report = compose_run_report(db, run_id)
    if report is None:
        raise HTTPException(status_code=404, detail="run not found")
    return report


@router.get("/runs/{run_id}/report/export")
def export_run_report(run_id: int, format: str = Query("markdown"), db: Session = Depends(get_db)):
    report = compose_run_report(db, run_id)
    if report is None:
        raise HTTPException(status_code=404, detail="run not found")
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


@router.get("/runs/{run_id}/report/cached")
def get_cached_run_report(run_id: int, db: Session = Depends(get_db)):
    """Return cached report if post-processed, otherwise compute live."""
    from backend.models.job import JobInstance
    job = db.get(JobInstance, run_id)
    if job and job.post_processed_at and job.report_json:
        return JSONResponse(content=job.report_json)

    report = compose_run_report(db, run_id)
    if report is None:
        raise HTTPException(status_code=404, detail="run not found")
    return JSONResponse(content=jsonable_encoder(_model_to_dict(report)))


# ── JIRA Draft ────────────────────────────────────────────────────────────────


@router.post("/runs/{run_id}/jira-draft", response_model=JiraDraftOut)
def create_run_jira_draft(run_id: int, db: Session = Depends(get_db)):
    report = compose_run_report(db, run_id)
    if report is None:
        raise HTTPException(status_code=404, detail="run not found")
    return build_jira_draft(report)


@router.get("/runs/{run_id}/jira-draft/cached")
def get_cached_jira_draft(run_id: int, db: Session = Depends(get_db)):
    """Return cached JIRA draft if post-processed, otherwise compute live."""
    from backend.models.job import JobInstance
    job = db.get(JobInstance, run_id)
    if job and job.post_processed_at and job.jira_draft_json:
        return JSONResponse(content=job.jira_draft_json)

    report = compose_run_report(db, run_id)
    if report is None:
        raise HTTPException(status_code=404, detail="run not found")
    draft = build_jira_draft(report)
    return JSONResponse(content=jsonable_encoder(_model_to_dict(draft)))


# ── Steps ─────────────────────────────────────────────────────────────────────


@router.get("/runs/{run_id}/steps", response_model=List[RunStepOut])
def list_run_steps(run_id: int, db: Session = Depends(get_db)):
    """Return step status for a JobInstance, mapped from StepTrace records."""
    from backend.models.job import JobInstance, StepTrace as _StepTrace

    job = db.get(JobInstance, run_id)
    if not job:
        raise HTTPException(status_code=404, detail="run not found")

    traces = (
        db.query(_StepTrace)
        .filter(_StepTrace.job_id == run_id)
        .order_by(_StepTrace.original_ts)
        .all()
    )

    if traces:
        step_latest: dict = {}
        for t in traces:
            step_latest[t.step_id] = t

        result = []
        for idx, (step_id, t) in enumerate(step_latest.items()):
            is_terminal = t.status in ("COMPLETED", "FAILED", "SKIPPED")
            result.append(RunStepOut(
                id=t.id,
                run_id=t.job_id,
                phase=t.stage,
                step_order=idx,
                name=step_id,
                action="",
                params={},
                status=t.status,
                started_at=t.original_ts,
                finished_at=t.created_at if is_terminal else None,
                exit_code=None,
                error_message=t.error_message,
                log_line_count=0,
                created_at=t.created_at,
            ))
        return result

    pipeline_def = job.pipeline_def or {}
    stages = pipeline_def.get("stages", {})
    now = datetime.utcnow()
    result = []
    idx = 0
    for stage_name, steps in stages.items():
        for step in (steps or []):
            result.append(RunStepOut(
                id=idx,
                run_id=run_id,
                phase=stage_name,
                step_order=idx,
                name=step.get("step_id", f"step_{idx}"),
                action=step.get("action", ""),
                params=step.get("params", {}),
                status="PENDING",
                started_at=None,
                finished_at=None,
                exit_code=None,
                error_message=None,
                log_line_count=0,
                created_at=now,
            ))
            idx += 1
    return result


@router.get("/runs/{run_id}/steps/{step_id}", response_model=RunStepOut)
def get_run_step(run_id: int, step_id: int, db: Session = Depends(get_db)):
    """Get a single step detail from StepTrace."""
    from backend.models.job import StepTrace as _StepTrace

    trace = db.query(_StepTrace).filter(_StepTrace.id == step_id, _StepTrace.job_id == run_id).first()
    if not trace:
        raise HTTPException(status_code=404, detail="step not found")
    is_terminal = trace.status in ("COMPLETED", "FAILED", "SKIPPED")
    return RunStepOut(
        id=trace.id,
        run_id=trace.job_id,
        phase=trace.stage,
        step_order=0,
        name=trace.step_id,
        action="",
        params={},
        status=trace.status,
        started_at=trace.original_ts,
        finished_at=trace.created_at if is_terminal else None,
        exit_code=None,
        error_message=trace.error_message,
        log_line_count=0,
        created_at=trace.created_at,
    )


# ── Artifacts ─────────────────────────────────────────────────────────────────


@router.get("/runs/{run_id}/artifacts/{artifact_id}/download")
def download_run_artifact(
    run_id: int,
    artifact_id: int,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
):
    from backend.models.job import JobArtifact

    artifact = db.get(JobArtifact, artifact_id)
    if not artifact or artifact.job_id != run_id:
        raise HTTPException(status_code=404, detail="artifact not found")

    target = _artifact_download_target(artifact.storage_uri)
    if target["kind"] == "redirect":
        return RedirectResponse(url=target["url"], status_code=307)

    local_path = Path(target["path"])
    media_type = "application/gzip" if local_path.suffixes[-2:] == [".tar", ".gz"] else None
    return FileResponse(path=str(local_path), filename=local_path.name, media_type=media_type)
