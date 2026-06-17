"""Host 运行日志归档概览（ADR-0025 Sprint 2）。"""

from __future__ import annotations

from typing import Any, Dict, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.models.host import Host
from backend.models.job import JobArtifact, JobInstance


def build_host_archive_status(db: Session, host_id: str) -> Dict[str, Any]:
    """汇总某 host 的 run_log_bundle 归档计数 + Agent 心跳指标。

    供 ``GET /api/v1/hosts/{host_id}/archive-status`` 使用。
    """
    host = db.get(Host, host_id)
    if host is None:
        raise LookupError(host_id)

    job_ids_subq = select(JobInstance.id).where(JobInstance.host_id == host_id)
    archived_total = db.execute(
        select(func.count(JobArtifact.id)).where(
            JobArtifact.artifact_type == "run_log_bundle",
            JobArtifact.job_id.in_(job_ids_subq),
        )
    ).scalar_one()
    last_archive_at = db.execute(
        select(func.max(JobArtifact.created_at)).where(
            JobArtifact.artifact_type == "run_log_bundle",
            JobArtifact.job_id.in_(job_ids_subq),
        )
    ).scalar_one()

    extra = host.extra if isinstance(host.extra, dict) else {}
    agent_metrics: Optional[Dict[str, Any]] = extra.get("archive")

    return {
        "host_id": host_id,
        "archived_total": int(archived_total or 0),
        "last_archive_at": last_archive_at.isoformat() if last_archive_at else None,
        "agent_metrics": agent_metrics,
    }
