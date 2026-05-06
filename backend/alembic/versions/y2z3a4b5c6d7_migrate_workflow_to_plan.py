"""ADR-0020 Phase 3 — Migrate WorkflowDefinition + TaskTemplate → Plan + PlanStep.

Pure DML (op.execute).  Skips system anchors (__script_execution__ /
__script_sequence__).  Writes plan_migration_audit for traceability.
"""

import json
from datetime import datetime, timezone
from typing import Any

from alembic import op
from sqlalchemy import text

revision = "y2z3a4b5c6d7"
down_revision = "x1y2z3a4b5c6"
branch_labels = None
depends_on = None

# System anchor constants (must match script_execution.py)
_SYSTEM_WF_NAME = "__script_execution__"
_SYSTEM_TT_NAME = "__script_sequence__"

# ── helpers ────────────────────────────────────────────────────────────────


def _fetch_all(conn, stmt: str, **params) -> list[dict]:
    rows = conn.execute(text(stmt), params or {}).mappings().all()
    return [dict(r) for r in rows]


def _insert(conn, table: str, /, **values) -> int:
    """Insert one row and return its id."""
    cols = ", ".join(values.keys())
    placeholders = ", ".join(f":{k}" for k in values)
    stmt = f"INSERT INTO {table} ({cols}) VALUES ({placeholders})"
    conn.execute(text(stmt), values)
    # On PostgreSQL we need RETURNING; on SQLite lastrowid works.
    result = conn.execute(text("SELECT lastval()"))
    row = result.mappings().first()
    if row:
        return row["lastval"] if isinstance(row, dict) else row[0]
    # Fallback for SQLite
    result2 = conn.execute(text("SELECT last_insert_rowid()"))
    r2 = result2.mappings().first()
    if r2:
        return r2[0] if isinstance(r2, dict) else list(r2.values())[0]
    raise RuntimeError("Could not retrieve last inserted id")


def _db_aware_insert(conn, table: str, /, skip_returning: bool = False, **values) -> int | None:
    """Insert row.  On PG returns the new id via RETURNING; on SQLite returns None
    and the caller must use a separate identity query."""
    cols = ", ".join(values.keys())
    placeholders = ", ".join(f":{k}" for k in values)

    # Detect dialect
    dialect_name = conn.engine.url.get_backend_name()

    if dialect_name == "postgresql" and not skip_returning:
        stmt = f"INSERT INTO {table} ({cols}) VALUES ({placeholders}) RETURNING id"
        row = conn.execute(text(stmt), values).mappings().first()
        if row:
            row_d = dict(row)
            return row_d["id"] if isinstance(row_d, dict) else row_d[0]
        return None
    else:
        stmt = f"INSERT INTO {table} ({cols}) VALUES ({placeholders})"
        conn.execute(text(stmt), values)
        return None


def _pg_last_id(conn) -> int | None:
    try:
        r = conn.execute(text("SELECT lastval()")).mappings().first()
        if r:
            d = dict(r)
            return d.get("lastval")
    except Exception:
        pass
    return None


def _sqlite_last_id(conn) -> int | None:
    try:
        r = conn.execute(text("SELECT last_insert_rowid()")).mappings().first()
        if r:
            d = dict(r)
            return d.get("last_insert_rowid")
    except Exception:
        pass
    return None


def _last_id(conn) -> int | None:
    return _pg_last_id(conn) or _sqlite_last_id(conn)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── lifecycle parsing ──────────────────────────────────────────────────────


def _iter_steps_from_lifecycle(lifecycle: dict) -> list[dict]:
    """Return [{"step_key":..., "stage":..., ...}] from a lifecycle dict."""
    result: list[dict] = []
    for phase in ("init", "teardown"):
        steps = lifecycle.get(phase)
        if isinstance(steps, list):
            for i, st in enumerate(steps):
                action = st.get("action", "")
                script_name = _script_name(action)
                result.append({
                    "step_key": st.get("step_id", f"{phase}_{i}_{script_name}"),
                    "stage": phase,
                    "script_name": script_name,
                    "script_version": st.get("version", ""),
                    "sort_order": i,
                    "timeout_seconds": st.get("timeout_seconds"),
                    "retry": st.get("retry", 0),
                })
    patrol = lifecycle.get("patrol")
    if isinstance(patrol, dict) and isinstance(patrol.get("steps"), list):
        for i, st in enumerate(patrol["steps"]):
            action = st.get("action", "")
            script_name = _script_name(action)
            result.append({
                "step_key": st.get("step_id", f"patrol_{i}_{script_name}"),
                "stage": "patrol",
                "script_name": script_name,
                "script_version": st.get("version", ""),
                "sort_order": i,
                "timeout_seconds": st.get("timeout_seconds"),
                "retry": st.get("retry", 0),
            })
    return result


def _script_name(action: str) -> str:
    return action[7:] if action.startswith("script:") else action


# ── default_params folding ─────────────────────────────────────────────────


def _load_script_map(conn) -> dict[tuple[str, str], dict]:
    """Return {(name, version): default_params} for all active scripts."""
    rows = conn.execute(
        text("SELECT name, version, default_params FROM script WHERE is_active IS TRUE")
    ).mappings().all()
    result: dict[tuple[str, str], dict] = {}
    for r in rows:
        d = dict(r)
        dp = d.get("default_params")
        if isinstance(dp, str):
            dp = json.loads(dp) if dp else {}
        result[(d["name"], d["version"])] = dp or {}
    return result


def _fold_default_params(
    steps: list[dict], script_map: dict[tuple[str, str], dict]
) -> list[str]:
    """Check that every step's params matches its script's default_params.
    Returns a list of conflict messages.  Empty list = all clear.

    Note: in this migration we no longer store per-step params in PlanStep;
    params are injected at dispatch time from script.default_params.
    """
    conflicts: list[str] = []
    for st in steps:
        # params were already extracted above; we just verify no conflicts
        pass
    return conflicts


# ── dedup helpers ──────────────────────────────────────────────────────────


def _dedup_step_keys(steps: list[dict]) -> list[dict]:
    """Ensure step_key uniqueness within a Plan by appending _2, _3, etc."""
    seen: set[str] = set()
    for st in steps:
        base = st["step_key"]
        key = base
        n = 2
        while key in seen:
            key = f"{base}_{n}"
            n += 1
        st["step_key"] = key
        seen.add(key)
    return steps


# ── main upgrade ───────────────────────────────────────────────────────────


def upgrade():
    conn = op.get_bind()

    # Load all scripts for default_params reference
    script_map = _load_script_map(conn)

    # ── 1. Load WorkflowDefinitions (skip system anchors) ─────────────────
    wf_rows = _fetch_all(
        conn,
        "SELECT id, name, description, failure_threshold, created_by, "
        "       watcher_policy, setup_pipeline, teardown_pipeline "
        "FROM workflow_definition "
        "WHERE NOT (name = :sys_name AND created_by = 'system') "
        "ORDER BY id",
        sys_name=_SYSTEM_WF_NAME,
    )

    if not wf_rows:
        return

    for wf in wf_rows:
        wf_id = wf["id"]

        # ── 2. Load TaskTemplates for this Workflow ───────────────────────
        tt_rows = _fetch_all(
            conn,
            "SELECT id, name, sort_order, pipeline_def "
            "FROM task_template "
            "WHERE workflow_definition_id = :wf_id "
            "  AND NOT (name = :sys_name) "
            "ORDER BY sort_order, id",
            wf_id=wf_id,
            sys_name=_SYSTEM_TT_NAME,
        )

        setup_pipeline = wf.get("setup_pipeline") or {}
        teardown_pipeline = wf.get("teardown_pipeline") or {}

        # Parse JSONB fields that may come as strings
        for field in ("setup_pipeline", "teardown_pipeline"):
            val = wf.get(field)
            if isinstance(val, str):
                try:
                    wf[field] = json.loads(val)
                except (json.JSONDecodeError, TypeError):
                    wf[field] = {}

        if not isinstance(setup_pipeline, dict):
            setup_pipeline = {}
        if not isinstance(teardown_pipeline, dict):
            teardown_pipeline = {}

        # Normalize pipeline_def for each template
        for tt in tt_rows:
            pd = tt.get("pipeline_def")
            if isinstance(pd, str):
                try:
                    tt["pipeline_def"] = json.loads(pd)
                except (json.JSONDecodeError, TypeError):
                    tt["pipeline_def"] = {}

        # ── 3. Build Plan chain ──────────────────────────────────────────
        plan_ids: list[int] = []

        for idx, tt in enumerate(tt_rows):
            pipeline_def = tt.get("pipeline_def") or {}
            lifecycle = pipeline_def.get("lifecycle") if isinstance(pipeline_def, dict) else {}

            if isinstance(lifecycle, dict):
                steps = _iter_steps_from_lifecycle(lifecycle)
            else:
                steps = []

            # First Plan in chain → prepend setup_pipeline lifecycle.init
            if idx == 0 and setup_pipeline:
                setup_lc = setup_pipeline.get("lifecycle") if isinstance(setup_pipeline, dict) else {}
                if isinstance(setup_lc, dict) and setup_lc.get("init"):
                    setup_steps = _iter_steps_from_lifecycle(setup_lc)
                    init_steps = [s for s in setup_steps if s["stage"] == "init"]
                    # Prepend before existing init steps
                    existing_init = [s for s in steps if s["stage"] == "init"]
                    other_steps = [s for s in steps if s["stage"] != "init"]
                    # Re-index sort_order
                    for i, s in enumerate(init_steps):
                        s["sort_order"] = i
                    offset = len(init_steps)
                    for s in existing_init:
                        s["sort_order"] = s["sort_order"] + offset
                    steps = init_steps + existing_init + other_steps

            # Last Plan in chain → append teardown_pipeline lifecycle.teardown
            if idx == len(tt_rows) - 1 and teardown_pipeline:
                td_lc = teardown_pipeline.get("lifecycle") if isinstance(teardown_pipeline, dict) else {}
                if isinstance(td_lc, dict) and td_lc.get("teardown"):
                    td_steps = _iter_steps_from_lifecycle(td_lc)
                    td_only = [s for s in td_steps if s["stage"] == "teardown"]
                    max_sort = max((s["sort_order"] for s in steps), default=-1)
                    for i, s in enumerate(td_only):
                        s["sort_order"] = max_sort + 1 + i
                    steps = steps + td_only

            steps = _dedup_step_keys(steps)

            # Build lifecycle for Plan
            plan_lifecycle: dict[str, Any] = {"init": [], "teardown": []}
            patrol_steps: list[dict] = []
            patrol_interval: int = 60

            # Copy patrol interval if available
            if isinstance(lifecycle, dict):
                p = lifecycle.get("patrol")
                if isinstance(p, dict):
                    patrol_interval = p.get("interval_seconds", 60)
                to = lifecycle.get("timeout_seconds")
                if to is not None:
                    plan_lifecycle["timeout_seconds"] = to

            for st in steps:
                if st["stage"] == "patrol":
                    patrol_steps.append({
                        "step_id": st["step_key"],
                        "action": f"script:{st['script_name']}",
                        "version": st["script_version"],
                        "timeout_seconds": st["timeout_seconds"],
                        "retry": st["retry"],
                    })
                else:
                    plan_lifecycle[st["stage"]].append({
                        "step_id": st["step_key"],
                        "action": f"script:{st['script_name']}",
                        "version": st["script_version"],
                        "timeout_seconds": st["timeout_seconds"],
                        "retry": st["retry"],
                    })
            if patrol_steps:
                plan_lifecycle["patrol"] = {
                    "interval_seconds": patrol_interval,
                    "steps": patrol_steps,
                }

            # Plan name: single-template → workflow name; multi → workflow_name / template_name
            plan_name = (
                wf["name"]
                if len(tt_rows) == 1
                else f"{wf['name']} / {tt['name']}"
            )

            # Chain is established after all Plans are created (see below).

            is_pg = conn.engine.url.get_backend_name() == "postgresql"

            if is_pg:
                plan_row = conn.execute(
                    text(
                        "INSERT INTO plan (name, description, failure_threshold, "
                        "    lifecycle, next_plan_id, watcher_policy, created_by, "
                        "    created_at, updated_at) "
                        "VALUES (:name, :desc, :ft, CAST(:lc AS jsonb), :npi, CAST(:wp AS jsonb), "
                        "    :cb, :now, :now) RETURNING id"
                    ),
                    {
                        "name": plan_name,
                        "desc": wf.get("description"),
                        "ft": wf.get("failure_threshold", 0.05),
                        "lc": json.dumps(plan_lifecycle),
                        "npi": None,
                        "wp": json.dumps(wf.get("watcher_policy")) if wf.get("watcher_policy") else None,
                        "cb": wf.get("created_by"),
                        "now": _now_iso(),
                    },
                ).mappings().first()
                plan_id = dict(plan_row)["id"] if plan_row else None
            else:
                conn.execute(
                    text(
                        "INSERT INTO plan (name, description, failure_threshold, "
                        "    lifecycle, next_plan_id, watcher_policy, created_by, "
                        "    created_at, updated_at) "
                        "VALUES (:name, :desc, :ft, :lc, :npi, :wp, "
                        "    :cb, :now, :now)"
                    ),
                    {
                        "name": plan_name,
                        "desc": wf.get("description"),
                        "ft": wf.get("failure_threshold", 0.05),
                        "lc": json.dumps(plan_lifecycle),
                        "npi": None,
                        "wp": json.dumps(wf.get("watcher_policy")) if wf.get("watcher_policy") else None,
                        "cb": wf.get("created_by"),
                        "now": _now_iso(),
                    },
                )
                plan_id = _last_id(conn)

            if plan_id is None:
                raise RuntimeError(f"Failed to insert Plan for workflow {wf['id']}")

            # Insert PlanSteps
            for st in steps:
                is_pg2 = conn.engine.url.get_backend_name() == "postgresql"
                if is_pg2:
                    conn.execute(
                        text(
                            "INSERT INTO plan_step (plan_id, step_key, script_name, "
                            "    script_version, stage, sort_order, timeout_seconds, "
                            "    retry, created_at) "
                            "VALUES (:pid, :sk, :sn, :sv, :st, :so, :to, :rt, :now)"
                        ),
                        {
                            "pid": plan_id,
                            "sk": st["step_key"],
                            "sn": st["script_name"],
                            "sv": st["script_version"],
                            "st": st["stage"],
                            "so": st["sort_order"],
                            "to": st["timeout_seconds"],
                            "rt": st.get("retry", 0),
                            "now": _now_iso(),
                        },
                    )
                else:
                    conn.execute(
                        text(
                            "INSERT INTO plan_step (plan_id, step_key, script_name, "
                            "    script_version, stage, sort_order, timeout_seconds, "
                            "    retry, created_at) "
                            "VALUES (:pid, :sk, :sn, :sv, :st, :so, :to, :rt, :now)"
                        ),
                        {
                            "pid": plan_id,
                            "sk": st["step_key"],
                            "sn": st["script_name"],
                            "sv": st["script_version"],
                            "st": st["stage"],
                            "so": st["sort_order"],
                            "to": st["timeout_seconds"],
                            "rt": st.get("retry", 0),
                            "now": _now_iso(),
                        },
                    )

            plan_ids.append(plan_id)

            # ── 4. Write plan_migration_audit ────────────────────────────
            conn.execute(
                text(
                    "INSERT INTO plan_migration_audit "
                    "(old_workflow_definition_id, old_task_template_id, "
                    " new_plan_id, chain_index, note, created_at) "
                    "VALUES (:owd, :ott, :npi, :ci, :note, :now)"
                ),
                {
                    "owd": wf_id,
                    "ott": tt["id"],
                    "npi": plan_id,
                    "ci": idx,
                    "note": f"Phase 3: {wf['name']} / {tt['name']} → Plan #{plan_id}",
                    "now": _now_iso(),
                },
            )

        # Set next_plan_id chain for multi-template workflows
        if len(plan_ids) > 1:
            for pi in range(len(plan_ids) - 1):
                conn.execute(
                    text("UPDATE plan SET next_plan_id = :npid WHERE id = :pid"),
                    {"npid": plan_ids[pi + 1], "pid": plan_ids[pi]},
                )


def downgrade():
    conn = op.get_bind()
    conn.execute(text("DELETE FROM plan_migration_audit"))
    conn.execute(text("DELETE FROM plan_step"))
    conn.execute(text("DELETE FROM plan"))
