"""Compact PlanRun report export — summary + devices + timeline (bounded)."""

from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.models.host import Device
from backend.models.job import JobInstance, StepTrace
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun

_EXPORT_MAX_JOBS = 500


def _iso(dt) -> Optional[str]:
    if dt is None:
        return None
    return dt.isoformat()


def build_plan_run_export(db: Session, pr: PlanRun) -> dict[str, Any]:
    """Aggregate a bounded export payload for markdown/json download."""
    plan = db.get(Plan, pr.plan_id)
    jobs = (
        db.query(JobInstance)
        .filter(JobInstance.plan_run_id == pr.id)
        .order_by(JobInstance.id.asc())
        .limit(_EXPORT_MAX_JOBS + 1)
        .all()
    )
    truncated = len(jobs) > _EXPORT_MAX_JOBS
    if truncated:
        jobs = jobs[:_EXPORT_MAX_JOBS]

    status_counts: dict[str, int] = {}
    for job in jobs:
        status_counts[job.status] = status_counts.get(job.status, 0) + 1
    total = sum(status_counts.values())
    pass_rate = status_counts.get("COMPLETED", 0) / total if total else 0.0

    device_serials: dict[int, str] = {}
    if jobs:
        device_ids = list({j.device_id for j in jobs})
        rows = db.execute(
            select(Device.id, Device.serial).where(Device.id.in_(device_ids))
        ).all()
        device_serials = {r.id: r.serial for r in rows}

    devices_out = [
        {
            "job_id": j.id,
            "device_id": j.device_id,
            "device_serial": device_serials.get(j.device_id),
            "host_id": j.host_id,
            "status": j.status,
            "status_reason": j.status_reason,
            "started_at": _iso(j.started_at),
            "ended_at": _iso(j.ended_at),
        }
        for j in jobs
    ]

    timeline_stages: list[dict[str, Any]] = []
    job_ids = [j.id for j in jobs]
    if job_ids:
        stage_rows = db.execute(
            select(
                StepTrace.stage,
                StepTrace.status,
                func.count(StepTrace.id),
            )
            .where(StepTrace.job_id.in_(job_ids))
            .group_by(StepTrace.stage, StepTrace.status)
        ).all()
        by_stage: dict[str, dict[str, int]] = {}
        for stage, status, cnt in stage_rows:
            by_stage.setdefault(stage, {})[status] = int(cnt)
        for stage in ("init", "patrol", "teardown"):
            counts = by_stage.get(stage, {})
            if not counts:
                continue
            succeeded = counts.get("COMPLETED", 0) + counts.get("SUCCESS", 0)
            failed = counts.get("FAILED", 0)
            timeline_stages.append({
                "stage": stage,
                "step_status_counts": counts,
                "device_succeeded": succeeded,
                "device_failed": failed,
            })

    return {
        "plan_run_id": pr.id,
        "plan_id": pr.plan_id,
        "plan_name": plan.name if plan else None,
        "status": pr.status,
        "run_type": pr.run_type,
        "started_at": _iso(pr.started_at),
        "ended_at": _iso(pr.ended_at),
        "failure_threshold": pr.failure_threshold,
        "result_summary": pr.result_summary,
        "summary": {
            "total_jobs": total,
            "status_counts": status_counts,
            "pass_rate": round(pass_rate, 4),
            "truncated": truncated,
            "max_jobs": _EXPORT_MAX_JOBS,
        },
        "devices": devices_out,
        "timeline": timeline_stages,
    }


def plan_run_export_to_markdown(data: dict[str, Any]) -> str:
    """Render export dict as markdown."""
    lines = [
        f"# PlanRun #{data['plan_run_id']} Report",
        "",
        f"- **Plan:** {data.get('plan_name') or '—'} (id={data.get('plan_id')})",
        f"- **Status:** {data.get('status')}",
        f"- **Run type:** {data.get('run_type')}",
        f"- **Started:** {data.get('started_at') or '—'}",
        f"- **Ended:** {data.get('ended_at') or '—'}",
        "",
        "## Summary",
        "",
    ]
    summary = data.get("summary") or {}
    lines.append(f"- Total jobs: {summary.get('total_jobs', 0)}")
    lines.append(f"- Pass rate: {summary.get('pass_rate', 0):.2%}")
    for status, cnt in sorted((summary.get("status_counts") or {}).items()):
        lines.append(f"- {status}: {cnt}")
    if summary.get("truncated"):
        lines.append(
            f"- _(devices truncated at {summary.get('max_jobs')} jobs)_"
        )

    lines.extend(["", "## Devices", ""])
    lines.append("| Job | Device | Host | Status |")
    lines.append("|-----|--------|------|--------|")
    for d in data.get("devices") or []:
        serial = d.get("device_serial") or d.get("device_id")
        lines.append(
            f"| {d.get('job_id')} | {serial} | {d.get('host_id')} | {d.get('status')} |"
        )

    lines.extend(["", "## Timeline", ""])
    for stage in data.get("timeline") or []:
        lines.append(
            f"### {stage.get('stage')} — "
            f"succeeded={stage.get('device_succeeded', 0)} "
            f"failed={stage.get('device_failed', 0)}"
        )

    return "\n".join(lines) + "\n"
