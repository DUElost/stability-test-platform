import asyncio
import json
import logging
import os
import shlex
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from urllib.parse import unquote, urlparse

import paramiko
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse
from pydantic import BaseModel, Field
from pydantic.fields import PrivateAttr
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session, selectinload

from backend.core.database import get_db
from backend.core.task_templates import list_templates as list_core_templates
from backend.models.enums import DeviceStatus
from backend.models.schemas import LogArtifact, RunStatus, Task, TaskRun
from backend.models.host import Device, Host
from backend.api.schemas import (
    AgentLogOut,
    AgentLogQuery,
    DeviceLiteOut,
    HostLiteOut,
    JiraDraftOut,
    LogArtifactIn,
    PaginatedResponse,
    RiskAlertOut,
    RunAgentOut,
    RunCompleteIn,
    RunOut,
    RunReportOut,
    RunStepOut,
    RunStepUpdate,
    RunUpdate,
    TaskCreate,
    TaskDispatch,
    TaskTemplateOut,
    TaskOut,
)
from backend.api.routes.auth import get_current_active_user, User, verify_agent_secret
from backend.core.audit import record_audit
from backend.services.report_service import (
    compose_run_report,
    build_jira_draft,
    build_risk_alerts as _build_risk_alerts,
    parse_run_log_summary as _parse_run_log_summary,
)
from backend.services.report_service import _load_risk_summary_from_artifacts, _model_to_dict
from backend.api.routes.websocket import broadcast_run_update, broadcast_task_update

router = APIRouter(prefix="/api/v1", tags=["tasks"])
logger = logging.getLogger(__name__)

DEVICE_LOCK_LEASE_SECONDS = int(os.getenv("DEVICE_LOCK_LEASE_SECONDS", "600"))

RUN_STATUS_ALIASES = {
    "COMPLETED": RunStatus.FINISHED.value,
    "CANCELLED": RunStatus.CANCELED.value,
}


def _parse_iso_timestamp(value: str) -> datetime:
    """Parse ISO timestamp while tolerating trailing `Z`."""
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


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


_WF_STATUS_TO_TASK = {
    "RUNNING":         "RUNNING",
    "SUCCESS":         "COMPLETED",
    "PARTIAL_SUCCESS": "COMPLETED",
    "FAILED":          "FAILED",
    "DEGRADED":        "FAILED",
}


def _wd_to_task_out(db: Session, wd, run_count: int = 0) -> TaskOut:
    from backend.models.workflow import WorkflowRun
    latest_run = (
        db.query(WorkflowRun)
        .filter(WorkflowRun.workflow_definition_id == wd.id)
        .order_by(WorkflowRun.id.desc())
        .first()
    )
    task_status = _WF_STATUS_TO_TASK.get(latest_run.status, "PENDING") if latest_run else "PENDING"
    return TaskOut(
        id=wd.id,
        name=wd.name,
        type="WORKFLOW",
        status=task_status,
        params={},
        priority=0,
        created_at=wd.created_at,
        runs_count=run_count,
    )


@router.get("/tasks", response_model=Any)
def list_tasks(
    request: Request,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    status: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    from sqlalchemy import func
    from backend.models.workflow import WorkflowDefinition, WorkflowRun

    # 单次聚合查询：run_count + 最新 run_id（避免 N+1）
    run_stats = (
        db.query(
            WorkflowRun.workflow_definition_id.label("wf_id"),
            func.count(WorkflowRun.id).label("run_count"),
            func.max(WorkflowRun.id).label("latest_run_id"),
        )
        .group_by(WorkflowRun.workflow_definition_id)
        .subquery("run_stats")
    )
    rows = (
        db.query(
            WorkflowDefinition,
            func.coalesce(run_stats.c.run_count, 0).label("run_count"),
            WorkflowRun.status.label("latest_status"),
        )
        .outerjoin(run_stats, WorkflowDefinition.id == run_stats.c.wf_id)
        .outerjoin(WorkflowRun, WorkflowRun.id == run_stats.c.latest_run_id)
        .order_by(WorkflowDefinition.id.desc())
        .all()
    )

    all_items: List[TaskOut] = []
    for wd, run_count, latest_status in rows:
        task_status = _WF_STATUS_TO_TASK.get(latest_status, "PENDING") if latest_status else "PENDING"
        if status and task_status != status:
            continue
        all_items.append(TaskOut(
            id=wd.id,
            name=wd.name,
            type="WORKFLOW",
            status=task_status,
            params={},
            priority=0,
            created_at=wd.created_at,
            runs_count=run_count,
        ))

    total = len(all_items)
    items = all_items[skip: skip + limit]

    if "skip" not in request.query_params and "limit" not in request.query_params:
        return items
    return PaginatedResponse(items=items, total=total, skip=skip, limit=limit)


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
def create_task(payload: TaskCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user), request: Request = None):
    raise HTTPException(status_code=503, detail="任务创建已迁移至 /orchestration/workflows，请通过工作流编辑器创建工作流")


@router.get("/tasks/{task_id}", response_model=TaskOut)
def get_task(task_id: int, db: Session = Depends(get_db)):
    from backend.models.workflow import WorkflowDefinition, WorkflowRun

    wd = db.get(WorkflowDefinition, task_id)
    if not wd:
        raise HTTPException(status_code=404, detail="task not found")

    run_count = db.query(WorkflowRun).filter(WorkflowRun.workflow_definition_id == task_id).count()
    return _wd_to_task_out(db, wd, run_count)


@router.get("/tasks/{task_id}/runs", response_model=Any)
def get_task_runs(
    task_id: int,
    request: Request,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    from backend.models.workflow import WorkflowDefinition, WorkflowRun
    from backend.models.job import JobInstance

    # task_id=0 作为“全量运行记录”兼容入口（供 task-runs/results 列表页使用）
    if task_id > 0:
        wd = db.get(WorkflowDefinition, task_id)
        if not wd:
            raise HTTPException(status_code=404, detail="task not found")

    base = (
        db.query(JobInstance, WorkflowRun.workflow_definition_id)
        .join(WorkflowRun, WorkflowRun.id == JobInstance.workflow_run_id)
    )
    if task_id > 0:
        base = base.filter(WorkflowRun.workflow_definition_id == task_id)

    ordered = base.order_by(JobInstance.id.desc())
    total = ordered.count()
    rows = ordered.offset(skip).limit(limit).all()
    if not rows:
        if "skip" not in request.query_params and "limit" not in request.query_params:
            return []
        return PaginatedResponse(items=[], total=0, skip=skip, limit=limit)

    _JOB_STATUS_MAP = {
        "PENDING":      "QUEUED",
        "RUNNING":      "RUNNING",
        "COMPLETED":    "FINISHED",
        "FAILED":       "FAILED",
        "ABORTED":      "CANCELED",
        "UNKNOWN":      "RUNNING",
        "PENDING_TOOL": "QUEUED",
    }
    run_items: List[RunOut] = []
    for job, workflow_definition_id in rows:
        status_out = _JOB_STATUS_MAP.get(job.status, job.status)
        host_id_out = 0
        if job.host_id is not None:
            try:
                host_id_out = int(str(job.host_id))
            except (TypeError, ValueError):
                host_id_out = 0
        run_items.append(RunOut(
            id=job.id,
            task_id=workflow_definition_id,
            host_id=host_id_out,
            device_id=job.device_id,
            status=status_out,
            progress=100 if status_out in {"FINISHED", "FAILED", "CANCELED"} else 0,
            progress_message=job.status_reason,
            started_at=job.started_at,
            finished_at=job.ended_at,
            exit_code=None,
            error_message=job.status_reason,
        ))

    if "skip" not in request.query_params and "limit" not in request.query_params:
        return run_items
    return PaginatedResponse(items=run_items, total=total, skip=skip, limit=limit)


@router.get("/logs/query", response_model=Any)
async def query_runtime_logs(
    job_id: Optional[int] = Query(None, ge=1),
    job_ids: Optional[str] = Query(None, description="Comma-separated job ids"),
    level: Optional[str] = Query(None),
    q: Optional[str] = Query(None, description="Keyword search"),
    step_id: Optional[str] = Query(None),
    from_ts: Optional[str] = Query(None, description="ISO8601 start time"),
    to_ts: Optional[str] = Query(None, description="ISO8601 end time"),
    cursor: Optional[str] = Query(None, description="Redis stream id cursor for older page"),
    limit: int = Query(200, ge=20, le=1000),
    current_user: User = Depends(get_current_active_user),
):
    """
    Query runtime logs from Redis stream with server-side filtering and pagination.

    Notes:
    - Results are returned in chronological order (old -> new) for direct rendering.
    - `cursor` points to the last scanned stream id from previous call.
    """
    del current_user
    try:
        from backend.main import redis_client
        if not redis_client:
            return {
                "items": [],
                "next_cursor": None,
                "has_more": False,
                "scanned": 0,
            }

        job_filter: Set[int] = set()
        if job_id:
            job_filter.add(job_id)
        if job_ids:
            for token in job_ids.split(","):
                token = token.strip()
                if not token:
                    continue
                try:
                    job_filter.add(int(token))
                except ValueError:
                    continue

        level_filter = (level or "").strip().upper()
        keyword = (q or "").strip().lower()
        step_filter = (step_id or "").strip().lower()

        from_dt: Optional[datetime] = None
        to_dt_dt: Optional[datetime] = None
        if from_ts:
            from_dt = _parse_iso_timestamp(from_ts.strip())
        if to_ts:
            to_dt_dt = _parse_iso_timestamp(to_ts.strip())

        # Use an exclusive max bound when paginating to older entries.
        redis_max = f"({cursor}" if cursor else "+"
        scan_count = max(limit * 8, 1200)
        entries = await redis_client.xrevrange("stp:logs", max=redis_max, min="-", count=scan_count)
        if not entries:
            return {
                "items": [],
                "next_cursor": None,
                "has_more": False,
                "scanned": 0,
            }

        items: List[Dict[str, Any]] = []
        for stream_id, fields in entries:
            job_text = fields.get("job_id") or fields.get("run_id")
            try:
                job_value = int(job_text) if job_text is not None else None
            except (TypeError, ValueError):
                job_value = None

            if job_filter and (job_value is None or job_value not in job_filter):
                continue

            level_value = str(fields.get("level") or "INFO").upper()
            if level_filter and level_filter != "ALL" and level_value != level_filter:
                continue

            step_value = str(fields.get("tag") or fields.get("step_id") or "")
            if step_filter and step_filter not in step_value.lower():
                continue

            timestamp_text = str(fields.get("timestamp") or "")
            if from_dt is not None or to_dt_dt is not None:
                try:
                    ts_dt = _parse_iso_timestamp(timestamp_text)
                except Exception:
                    continue
                if from_dt is not None and ts_dt < from_dt:
                    continue
                if to_dt_dt is not None and ts_dt > to_dt_dt:
                    continue

            message_value = str(fields.get("message") or "")
            if keyword:
                haystack = f"{message_value}\n{step_value}\n{job_text or ''}".lower()
                if keyword not in haystack:
                    continue

            items.append({
                "stream_id": stream_id,
                "job_id": job_value,
                "step_id": step_value,
                "level": level_value,
                "timestamp": timestamp_text,
                "message": message_value,
            })
            if len(items) >= limit:
                break

        # Reverse so frontend gets chronological order.
        items.reverse()

        next_cursor = entries[-1][0] if len(entries) >= scan_count else None
        return {
            "items": items,
            "next_cursor": next_cursor,
            "has_more": next_cursor is not None,
            "scanned": len(entries),
        }
    except Exception as e:
        logger.warning("query_runtime_logs_failed: %s", e)
        raise HTTPException(status_code=500, detail="failed to query runtime logs")


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


@router.post("/runs/{run_id}/jira-draft", response_model=JiraDraftOut)
def create_run_jira_draft(run_id: int, db: Session = Depends(get_db)):
    report = compose_run_report(db, run_id)
    if report is None:
        raise HTTPException(status_code=404, detail="run not found")
    return build_jira_draft(report)


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
def dispatch_task(task_id: int, payload: TaskDispatch, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user), request: Request = None):
    raise HTTPException(status_code=503, detail="任务分发已迁移至 /orchestration/workflows/{id}/dispatch")


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
def cancel_task(task_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user), request: Request = None):
    raise HTTPException(status_code=503, detail="取消操作已迁移至 /orchestration/workflow-runs/{id}/abort")


@router.post("/tasks/{task_id}/retry", response_model=TaskOut)
def retry_task(task_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    raise HTTPException(status_code=503, detail="重试操作已迁移至 /orchestration/workflows/{id}/dispatch")


# ---------------------------------------------------------------------------
# Batch Operations
# ---------------------------------------------------------------------------

class BatchTaskIds(BaseModel):
    task_ids: List[int]


class BatchResult(BaseModel):
    success: List[int] = Field(default_factory=list)
    failed: List[dict] = Field(default_factory=list)
    total: int = 0


@router.post("/tasks/batch/cancel", response_model=BatchResult)
def batch_cancel_tasks(
    payload: BatchTaskIds,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    raise HTTPException(status_code=503, detail="批量取消已迁移至 /orchestration 相关接口")


@router.post("/tasks/batch/retry", response_model=BatchResult)
def batch_retry_tasks(
    payload: BatchTaskIds,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    raise HTTPException(status_code=503, detail="批量重试已迁移至 /orchestration 相关接口")


@router.delete("/tasks/{task_id}")
def delete_task(
    task_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    raise HTTPException(status_code=503, detail="删除操作已迁移至 /orchestration/workflows/{id}")


# ==================== RunStep API (Pipeline 子步骤) ====================


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
        # Deduplicate by step_id: keep the latest event for each step
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

    # No traces yet: synthesize PENDING steps from pipeline_def stages
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
