"""ADR-0020 Phase 4 — Migrate WorkflowRun → PlanRun + backfill job_instance FKs.

Pure DML (op.execute).  Skips script_execution history.  Chain PlanRuns for
multi-template workflows via parent_plan_run_id / root_plan_run_id.
"""

import json
from datetime import datetime, timezone
from typing import Any

from alembic import op
from sqlalchemy import text

revision = "z3a4b5c6d7e8"
down_revision = "y2z3a4b5c6d7"
branch_labels = None
depends_on = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fetch_all(conn, stmt: str, **params) -> list[dict]:
    rows = conn.execute(text(stmt), params or {}).mappings().all()
    return [dict(r) for r in rows]


def _pg_last_id(conn) -> int | None:
    try:
        r = conn.execute(text("SELECT lastval()")).mappings().first()
        if r:
            return dict(r).get("lastval")
    except Exception:
        return None


def _sqlite_last_id(conn) -> int | None:
    try:
        r = conn.execute(text("SELECT last_insert_rowid()")).mappings().first()
        if r:
            return dict(r).get("last_insert_rowid")
    except Exception:
        return None


def _last_id(conn) -> int | None:
    return _pg_last_id(conn) or _sqlite_last_id(conn)


def _plan_snapshot(conn, plan_id: int) -> dict:
    """Build plan_snapshot from current Plan + PlanStep rows."""
    plan_rows = _fetch_all(
        conn,
        "SELECT id, name, description, failure_threshold, lifecycle, "
        "       watcher_policy FROM plan WHERE id = :pid",
        pid=plan_id,
    )
    if not plan_rows:
        return {}
    plan = plan_rows[0]
    lc = plan.get("lifecycle") or {}
    if isinstance(lc, str):
        try:
            lc = json.loads(lc)
        except Exception:
            lc = {}

    return {
        "plan_id": plan["id"],
        "name": plan["name"],
        "failure_threshold": plan.get("failure_threshold", 0.05),
        "lifecycle": lc,
        "watcher_policy": plan.get("watcher_policy"),
        "note": {"snapshot_synthesized": True},
    }


def upgrade():
    conn = op.get_bind()
    is_pg = conn.engine.url.get_backend_name() == "postgresql"

    # ── Load plan_migration_audit for mapping ──────────────────────────────
    audit_rows = _fetch_all(
        conn,
        "SELECT old_workflow_definition_id, old_task_template_id, "
        "       new_plan_id, chain_index "
        "FROM plan_migration_audit "
        "ORDER BY old_workflow_definition_id, chain_index",
    )

    # Build maps: wf_id → [plan_ids sorted by chain_index]
    wf_to_plans: dict[int, list[int]] = {}
    # (wf_id, tt_id) → plan_id
    tt_to_plan: dict[tuple[int, int], int] = {}
    for a in audit_rows:
        wf_id = a["old_workflow_definition_id"]
        tt_id = a["old_task_template_id"]
        plan_id = a["new_plan_id"]
        wf_to_plans.setdefault(wf_id, []).append(plan_id)
        tt_to_plan[(wf_id, tt_id)] = plan_id

    # ── Check schedule associations for SCHEDULE run_type ────────────────
    sched_rows = _fetch_all(
        conn,
        "SELECT DISTINCT workflow_definition_id, task_template_id "
        "FROM task_schedules "
        "WHERE workflow_definition_id IS NOT NULL OR task_template_id IS NOT NULL",
    )
    sched_wf_ids: set[int] = set()
    sched_tt_keys: set[tuple[int, int]] = set()
    for s in sched_rows:
        if s["workflow_definition_id"]:
            sched_wf_ids.add(s["workflow_definition_id"])
        if s["task_template_id"] and s["workflow_definition_id"]:
            sched_tt_keys.add((s["workflow_definition_id"], s["task_template_id"]))

    # ── Load WorkflowRuns (skip script_execution) ─────────────────────────
    wr_rows = _fetch_all(
        conn,
        "SELECT id, workflow_definition_id, status, failure_threshold, "
        "       triggered_by, started_at, ended_at, result_summary, "
        "       run_type, run_context "
        "FROM workflow_run "
        "WHERE run_type IS DISTINCT FROM 'script_execution' "
        "ORDER BY id",
    )

    for wr in wr_rows:
        wr_id = wr["id"]
        wf_id = wr["workflow_definition_id"]
        plan_ids = wf_to_plans.get(wf_id)

        if not plan_ids:
            # No Plan mapping — skip this run (shouldn't happen if preflight passed)
            continue

        # Determine run_type
        run_type = "MANUAL"
        if wr.get("run_type") in ("MANUAL", "SCHEDULE", "CHAIN"):
            run_type = wr["run_type"]

        # ── Create PlanRun(s) ─────────────────────────────────────────────
        pr_ids: list[int] = []
        root_id: int | None = None

        for ci, plan_id in enumerate(plan_ids):
            sn = _plan_snapshot(conn, plan_id)
            sn_json = json.dumps(sn) if sn else "{}"
            rc = wr.get("run_context")
            rc_json = json.dumps(rc) if rc and isinstance(rc, dict) else None
            rs = wr.get("result_summary")
            rs_json = json.dumps(rs) if rs and isinstance(rs, dict) else None

            segment_run_type = run_type
            if len(plan_ids) > 1 and ci > 0:
                segment_run_type = "CHAIN"
            elif len(plan_ids) > 1 and ci == 0 and segment_run_type == "MANUAL":
                segment_run_type = "MANUAL"

            if is_pg:
                row = conn.execute(
                    text(
                        "INSERT INTO plan_run (plan_id, status, failure_threshold, "
                        "    plan_snapshot, run_type, run_context, triggered_by, "
                        "    started_at, ended_at, result_summary, "
                        "    parent_plan_run_id, root_plan_run_id, chain_index) "
                        "VALUES (:pid, :st, :ft, CAST(:ps AS jsonb), :rt, CAST(:rc AS jsonb), :tb, "
                        "    :sa, :ea, CAST(:rs AS jsonb), :prid, :rid, :ci) RETURNING id"
                    ),
                    {
                        "pid": plan_id,
                        "st": wr["status"],
                        "ft": wr.get("failure_threshold", 0.05),
                        "ps": sn_json,
                        "rt": segment_run_type,
                        "rc": rc_json,
                        "tb": wr.get("triggered_by"),
                        "sa": wr.get("started_at"),
                        "ea": wr.get("ended_at"),
                        "rs": rs_json,
                        "prid": None,
                        "rid": None,
                        "ci": ci,
                    },
                ).mappings().first()
                pr_id = dict(row)["id"] if row else None
            else:
                conn.execute(
                    text(
                        "INSERT INTO plan_run (plan_id, status, failure_threshold, "
                        "    plan_snapshot, run_type, run_context, triggered_by, "
                        "    started_at, ended_at, result_summary, "
                        "    parent_plan_run_id, root_plan_run_id, chain_index) "
                        "VALUES (:pid, :st, :ft, :ps, :rt, :rc, :tb, "
                        "    :sa, :ea, :rs, :prid, :rid, :ci)"
                    ),
                    {
                        "pid": plan_id,
                        "st": wr["status"],
                        "ft": wr.get("failure_threshold", 0.05),
                        "ps": sn_json,
                        "rt": segment_run_type,
                        "rc": rc_json,
                        "tb": wr.get("triggered_by"),
                        "sa": wr.get("started_at"),
                        "ea": wr.get("ended_at"),
                        "rs": rs_json,
                        "prid": None,
                        "rid": None,
                        "ci": ci,
                    },
                )
                pr_id = _last_id(conn)

            if pr_id is None:
                raise RuntimeError(
                    f"Failed to insert PlanRun for workflow_run {wr_id}, plan {plan_id}"
                )

            pr_ids.append(pr_id)
            if root_id is None:
                root_id = pr_id

        # ── Set parent_plan_run_id / root_plan_run_id for chain ──────────
        if len(pr_ids) > 1:
            for ci, pr_id in enumerate(pr_ids):
                parent = pr_ids[ci - 1] if ci > 0 else None
                conn.execute(
                    text(
                        "UPDATE plan_run SET parent_plan_run_id = :prid, "
                        "    root_plan_run_id = :rid WHERE id = :id"
                    ),
                    {"prid": parent, "rid": root_id, "id": pr_id},
                )

        # ── Backfill job_instance ─────────────────────────────────────────
        # For each old JobInstance in this WorkflowRun, find the matching
        # new PlanRun via task_template_id → plan_migration_audit.
        jobs = _fetch_all(
            conn,
            "SELECT id, task_template_id FROM job_instance "
            "WHERE workflow_run_id = :wrid",
            wrid=wr_id,
        )

        for job in jobs:
            tt_id = job["task_template_id"]
            plan_id = tt_to_plan.get((wf_id, tt_id)) if tt_id else None

            if plan_id is None:
                raise RuntimeError(
                    f"JobInstance {job['id']} (wr={wr_id}, tt={tt_id}) "
                    f"has no Plan mapping in plan_migration_audit"
                )

            # Find the PlanRun for this plan_id within the current chain
            pr_for_job = None
            for ci, pid in enumerate(plan_ids):
                if pid == plan_id and ci < len(pr_ids):
                    pr_for_job = pr_ids[ci]
                    break

            if pr_for_job is None:
                raise RuntimeError(
                    f"JobInstance {job['id']}: plan_id={plan_id} not found "
                    f"in chain {pr_ids}"
                )

            conn.execute(
                text(
                    "UPDATE job_instance SET plan_run_id = :prid, "
                    "    plan_id = :pid WHERE id = :jid"
                ),
                {"prid": pr_for_job, "pid": plan_id, "jid": job["id"]},
            )

    # ── Assertion checks ───────────────────────────────────────────────────
    result = conn.execute(
        text(
            "SELECT COUNT(*) AS cnt FROM job_instance ji "
            "JOIN workflow_run wr ON ji.workflow_run_id = wr.id "
            "WHERE wr.run_type IS DISTINCT FROM 'script_execution' "
            "  AND (ji.plan_run_id IS NULL OR ji.plan_id IS NULL)"
        )
    ).mappings().first()
    missing = dict(result)["cnt"] if result else 0
    if missing > 0:
        raise RuntimeError(
            f"Phase 4 consistency failure: {missing} JobInstance(s) "
            f"still have NULL plan_run_id/plan_id"
        )


def downgrade():
    conn = op.get_bind()
    conn.execute(text("UPDATE job_instance SET plan_run_id = NULL, plan_id = NULL"))
    conn.execute(text("DELETE FROM plan_run"))
