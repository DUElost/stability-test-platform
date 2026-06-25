"""Resolve expected script manifests from plan_snapshot."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models.plan_run import PlanRun
from backend.models.script import Script


def expected_scripts_for_run(plan_run: PlanRun, db: Session) -> list[dict]:
    """Build ``[{name, version, sha256, nfs_path}]`` from plan_snapshot ∩ Script table."""
    snapshot = plan_run.plan_snapshot or {}
    snapshot_steps = snapshot.get("steps") or []
    keys = {(s["script_name"], s["script_version"]) for s in snapshot_steps}
    if not keys:
        return []

    rows = db.execute(
        select(
            Script.name,
            Script.version,
            Script.content_sha256,
            Script.nfs_path,
        ).where(Script.is_active.is_(True))
    ).all()
    return [
        {
            "name": r.name,
            "version": r.version,
            "sha256": r.content_sha256 or "",
            "nfs_path": r.nfs_path or "",
        }
        for r in rows
        if (r.name, r.version) in keys
    ]


_expected_scripts_for_run = expected_scripts_for_run
