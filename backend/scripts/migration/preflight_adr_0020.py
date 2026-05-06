"""ADR-0020 Phase 1 — Preflight validation script.

Run BEFORE the maintenance window.  Blocks Phase 2+ when any blocking
condition is found (exit code != 0).

Usage:
    python -m backend.scripts.migration.preflight_adr_0020 [--json] [--csv-dir DIR]

Outputs:
    - Text report to stdout
    - If --json: JSON summary to <csv-dir>/preflight_summary.json
    - If --csv-dir: per-category CSV files to DIR
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker


def _get_db_url() -> str:
    return os.getenv(
        "DATABASE_URL",
        "postgresql://stability:stability@localhost:5432/stability",
    )


# ── helpers ────────────────────────────────────────────────────────────────

def _build_session() -> Session:
    url = _get_db_url()
    engine = create_engine(url)
    SessionLocal = sessionmaker(bind=engine)
    return SessionLocal()


def _parse_json_field(value: Any) -> Any:
    """Normalize a JSON/JSONB column value to a Python object.

    PostgreSQL returns dicts; SQLite stores JSONB as TEXT and returns str.
    """
    if isinstance(value, str):
        return json.loads(value)
    if value is None:
        return None
    return value


def _query_csv(db: Session, sql: str, **params) -> List[Dict[str, Any]]:
    """Execute raw SQL and return result as list of dicts (for CSV output)."""
    result = db.execute(text(sql), params or {})
    if result.returns_rows:
        return [dict(row._mapping) for row in result]
    return []


class _Blocker:
    """Accumulate blocking/non-blocking issues."""

    def __init__(self) -> None:
        self.blocks: List[str] = []
        self.warnings: List[str] = []

    def block(self, msg: str) -> None:
        self.blocks.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    @property
    def blocked(self) -> bool:
        return len(self.blocks) > 0


# ── checks ─────────────────────────────────────────────────────────────────

# Expected "agent_version" key written by ADR-0020 heartbeat path.
# If no hosts have reported agent_version, the version-consistency check
# must block because the data source is unavailable.
EXPECTED_AGENT_VERSION_KEY = "agent_version"

# Sentinel WorkflowDefinition / TaskTemplate names used by the legacy
# script_execution path (script_execution.py L19-20).
SYSTEM_WORKFLOW_NAME = "__script_execution__"
SYSTEM_TEMPLATE_NAME = "__script_sequence__"

# Tables whose orphan references we scan.
CHILD_TABLES = [
    ("step_trace",           "job_id",         "job_instance",   "id"),
    ("job_artifact",         "job_id",         "job_instance",   "id"),
    ("job_log_signal",       "job_id",         "job_instance",   "id"),
    ("resource_allocation",  "job_instance_id", "job_instance",  "id"),
    ("device_leases",        "job_id",         "job_instance",   "id"),
]

# Columns that must NOT exist on the listed tables after Phase 5.
REDUNDANT_COLUMNS = {
    "job_instance":       ["workflow_run_id", "task_template_id"],
    "workflow_run":       ["workflow_definition_id"],
    "task_template":      ["workflow_definition_id"],
    "task_schedules":     ["workflow_definition_id", "task_template_id",
                           "tool_id", "task_type"],
}


def _check_workflow_counts(db: Session, b: _Blocker) -> Dict[str, Any]:
    """Count WorkflowDefinitions and multi-template workflows."""
    total_wf = db.execute(text(
        "SELECT COUNT(*) FROM workflow_definition"
    )).scalar() or 0

    total_tt = db.execute(text(
        "SELECT COUNT(*) FROM task_template"
    )).scalar() or 0

    multi = _query_csv(db, """
        SELECT wd.id, wd.name, COUNT(tt.id) AS template_count
          FROM workflow_definition wd
          JOIN task_template tt ON tt.workflow_definition_id = wd.id
         GROUP BY wd.id, wd.name
        HAVING COUNT(tt.id) > 1
         ORDER BY wd.id
    """)

    schedules = _query_csv(db, """
        SELECT ts.id, ts.name, ts.workflow_definition_id, ts.task_template_id,
               ts.task_type, ts.enabled, ts.cron_expression
          FROM task_schedules ts
         WHERE ts.workflow_definition_id IS NOT NULL
            OR ts.task_template_id IS NOT NULL
            OR ts.tool_id IS NOT NULL
         ORDER BY ts.id
    """)

    if multi:
        b.warn(f"{len(multi)} workflows have multiple task templates")

    return {
        "total_workflow_definitions": total_wf,
        "total_task_templates": total_tt,
        "multi_template_workflows": multi,
        "old_task_schedules": schedules,
    }


def _validate_pipeline_defs(db: Session, b: _Blocker) -> List[Dict[str, Any]]:
    """Static validation of pipeline_def on every TaskTemplate."""
    rows = _query_csv(db, """
        SELECT tt.id, tt.name, tt.workflow_definition_id, tt.pipeline_def
          FROM task_template tt
         ORDER BY tt.id
    """)
    issues: List[Dict[str, Any]] = []
    for row in rows:
        pd = _parse_json_field(row["pipeline_def"]) or {}
        if not isinstance(pd, dict):
            b.block(f"TaskTemplate {row['id']}: pipeline_def is not a dict")
            issues.append({**row, "issue": "pipeline_def not a dict"})
            continue

        lifecycle = pd.get("lifecycle")
        if not lifecycle:
            b.block(f"TaskTemplate {row['id']}: missing lifecycle top-level key")
            issues.append({**row, "issue": "missing lifecycle"})
            continue

        init_steps = lifecycle.get("init") or []
        if not init_steps:
            b.block(f"TaskTemplate {row['id']}: lifecycle.init is empty")
            issues.append({**row, "issue": "init empty"})

        patrol = lifecycle.get("patrol")
        if patrol and isinstance(patrol, dict):
            interval = patrol.get("interval_seconds")
            if not isinstance(interval, (int, float)) or interval <= 0:
                b.warn(f"TaskTemplate {row['id']}: patrol.interval_seconds "
                       f"invalid ({interval})")

        for stage_name in ("init", "teardown"):
            steps = lifecycle.get(stage_name) or []
            if isinstance(steps, list):
                for idx, step in enumerate(steps):
                    _validate_step(b, issues, row, stage_name, idx, step)

        patrol_steps = (patrol or {}).get("steps") or []
        for idx, step in enumerate(patrol_steps):
            _validate_step(b, issues, row, "patrol", idx, step)

    return issues


def _validate_step(
    b: _Blocker, issues: list, row: dict,
    stage: str, idx: int, step: dict,
) -> None:
    sid = step.get("step_id", f"#{idx}")
    tag = f"TaskTemplate {row['id']} {stage}[{sid}]"

    action = step.get("action", "")
    if not action.startswith("script:"):
        b.block(f"{tag}: action '{action}' is not script:<name>")
        issues.append({"task_template_id": row["id"], "stage": stage,
                       "step_index": idx, "issue": f"bad action: {action}"})
        return

    if not step.get("version"):
        b.block(f"{tag}: missing version")
        issues.append({"task_template_id": row["id"], "stage": stage,
                       "step_index": idx, "issue": "missing version"})


def _check_script_refs(db: Session, b: _Blocker) -> List[Dict[str, Any]]:
    """Find steps referencing non-existent or inactive Script rows."""
    tts = _query_csv(db, """
        SELECT id, name, workflow_definition_id, pipeline_def
          FROM task_template
         ORDER BY id
    """)
    scripts = _query_csv(db, """
        SELECT name, version, is_active FROM script
    """)
    script_keys = {(s["name"], s["version"]) for s in scripts}
    inactive_keys = {(s["name"], s["version"])
                     for s in scripts if not s["is_active"]}

    missing: List[Dict[str, Any]] = []
    for tt in tts:
        pd = _parse_json_field(tt["pipeline_def"]) or {}
        lifecycle = pd.get("lifecycle") or {}
        for stage_name in ("init", "teardown"):
            for idx, step in enumerate(lifecycle.get(stage_name) or []):
                _check_step_script(step, tt, stage_name, idx,
                                   script_keys, inactive_keys, b, missing)
        patrol = lifecycle.get("patrol") or {}
        for idx, step in enumerate((patrol).get("steps") or []):
            _check_step_script(step, tt, "patrol", idx,
                               script_keys, inactive_keys, b, missing)
    return missing


def _check_step_script(
    step: dict, tt: dict, stage: str, idx: int,
    script_keys: set, inactive_keys: set,
    b: _Blocker, missing: list,
) -> None:
    action = step.get("action", "")
    if not action.startswith("script:"):
        return
    name = action[len("script:"):]
    version = step.get("version", "")
    key = (name, version)
    if key not in script_keys:
        b.block(f"TaskTemplate {tt['id']} {stage}[{idx}]: "
                f"script {name} v{version} not found")
        missing.append({"task_template_id": tt["id"], "stage": stage,
                        "step_index": idx, "script_name": name,
                        "version": version, "issue": "missing"})
    elif key in inactive_keys:
        b.warn(f"TaskTemplate {tt['id']} {stage}[{idx}]: "
               f"script {name} v{version} is inactive")


def _check_default_params_conflicts(db: Session, b: _Blocker) -> List[Dict[str, Any]]:
    """Pre-compute default_params conflicts for every step."""
    tts = _query_csv(db, """
        SELECT id, name, pipeline_def FROM task_template ORDER BY id
    """)
    scripts = _query_csv(db, """
        SELECT name, version, default_params, is_active FROM script
    """)
    script_defaults: Dict[Tuple[str, str], dict] = {}
    for s in scripts:
        script_defaults[(s["name"], s["version"])] = _parse_json_field(s["default_params"]) or {}

    conflicts: List[Dict[str, Any]] = []
    for tt in tts:
        pd = _parse_json_field(tt["pipeline_def"]) or {}
        lifecycle = pd.get("lifecycle") or {}
        for stage_name in ("init", "teardown"):
            for idx, step in enumerate(lifecycle.get(stage_name) or []):
                _check_step_param_conflict(
                    step, tt, stage_name, idx, script_defaults, conflicts)
        patrol = lifecycle.get("patrol") or {}
        for idx, step in enumerate((patrol).get("steps") or []):
            _check_step_param_conflict(
                step, tt, "patrol", idx, script_defaults, conflicts)
    return conflicts


def _check_step_param_conflict(
    step: dict, tt: dict, stage: str, idx: int,
    script_defaults: dict, conflicts: list,
) -> None:
    action = step.get("action", "")
    if not action.startswith("script:"):
        return
    name = action[len("script:"):]
    version = step.get("version", "")
    defaults = script_defaults.get((name, version))
    if defaults is None:
        return  # handled by _check_script_refs
    step_params = step.get("params") or {}
    if not step_params:
        return
    if step_params == defaults:
        return  # can be folded, no conflict
    conflicts.append({
        "task_template_id": tt["id"],
        "stage": stage,
        "step_index": idx,
        "script_name": name,
        "version": version,
        "step_params": json.dumps(step_params),
        "default_params": json.dumps(defaults),
    })


def _check_active_jobs(db: Session, b: _Blocker) -> List[Dict[str, Any]]:
    """Block if any job is in a non-terminal state."""
    active = _query_csv(db, """
        SELECT id, status, workflow_run_id, device_id, host_id,
               started_at, updated_at
          FROM job_instance
         WHERE status IN ('PENDING','RUNNING','UNKNOWN')
         ORDER BY id
    """)
    for job in active:
        b.block(f"Active JobInstance {job['id']}: status={job['status']}")
    return active


def _check_orphan_refs(db: Session, b: _Blocker) -> Dict[str, List[Dict[str, Any]]]:
    """Scan child tables for orphan references (FK points to missing parent)."""
    orphans: Dict[str, list] = {}
    for child_table, child_col, parent_table, parent_col in CHILD_TABLES:
        sql = (
            f"SELECT c.* FROM {child_table} c "
            f"LEFT JOIN {parent_table} p ON c.{child_col} = p.{parent_col} "
            f"WHERE c.{child_col} IS NOT NULL AND p.{parent_col} IS NULL"
        )
        rows = _query_csv(db, sql)
        if rows:
            b.block(f"{child_table}: {len(rows)} orphan references to {parent_table}")
            orphans[child_table] = rows[:200]  # cap per table
    return orphans


def _check_device_lease_active_legacy(db: Session, b: _Blocker) -> List[Dict[str, Any]]:
    """Find ACTIVE leases on legacy script_execution JobInstances."""
    rows = _query_csv(db, """
        SELECT dl.*
          FROM device_leases dl
          JOIN job_instance ji ON dl.job_id = ji.id
          JOIN workflow_run wr ON ji.workflow_run_id = wr.id
         WHERE dl.status = 'ACTIVE'
           AND wr.run_type = 'script_execution'
    """)
    if rows:
        b.block(f"{len(rows)} ACTIVE leases on script_execution jobs "
                "(Phase 5 will release them)")
    return rows


def _check_redundant_columns(db: Session, b: _Blocker) -> Dict[str, List[str]]:
    """Check that redundant columns exist (will be dropped in Phase 5)."""
    present: Dict[str, List[str]] = {}
    for table, cols in REDUNDANT_COLUMNS.items():
        found: List[str] = []
        for col in cols:
            try:
                rows = _query_csv(db, """
                    SELECT column_name
                      FROM information_schema.columns
                     WHERE table_name = :tbl AND column_name = :col
                """, tbl=table, col=col)
            except Exception:
                # Non-PostgreSQL backend (e.g. SQLite tests) — skip
                continue
            if rows:
                found.append(col)
        if found:
            present[table] = found
    # Non-blocking: these columns SHOULD exist at this point.
    return present


def _check_script_execution_history(db: Session, b: _Blocker) -> List[Dict[str, Any]]:
    """Archive-list script_execution WorkflowRuns (will NOT be migrated)."""
    rows = _query_csv(db, """
        SELECT wr.id, wr.status, wr.started_at,
               COUNT(ji.id) AS job_count
          FROM workflow_run wr
          LEFT JOIN job_instance ji ON ji.workflow_run_id = wr.id
         WHERE wr.run_type = 'script_execution'
         GROUP BY wr.id, wr.status, wr.started_at
         ORDER BY wr.id
    """)
    if rows:
        b.warn(f"{len(rows)} script_execution WorkflowRuns will be discarded "
               "(preflight archive only)")
    return rows


def _check_script_sequences(db: Session, b: _Blocker) -> List[Dict[str, Any]]:
    """Archive-list ScriptSequence rows (will NOT be migrated to Plan)."""
    rows = _query_csv(db, """
        SELECT id, name, on_failure, created_by, created_at
          FROM script_sequence
         ORDER BY id
    """)
    if rows:
        b.warn(f"{len(rows)} ScriptSequence rows will be discarded "
               "(preflight archive only)")
    return rows


def _check_agent_version_consistency(db: Session, b: _Blocker) -> Dict[str, Any]:
    """Verify all online agents report the same agent_version."""
    hosts = _query_csv(db, """
        SELECT id, hostname, ip, status, extra
          FROM host
         WHERE status = 'ONLINE'
         ORDER BY id
    """)

    versions: Dict[str, List[str]] = {}
    versionless: List[str] = []
    for h in hosts:
        extra = _parse_json_field(h.get("extra")) or {}
        ver = extra.get(EXPECTED_AGENT_VERSION_KEY)
        if ver:
            versions.setdefault(ver, []).append(h["id"])
        else:
            versionless.append(h["id"])

    if versionless and versions:
        # Some have version, some don't — block.
        b.block(f"{len(versionless)} online hosts missing agent_version "
                f"(hosts: {versionless})")
    elif versionless and not versions:
        b.block(f"ALL {len(versionless)} online hosts missing agent_version "
                "— heartbeat source unavailable")

    if len(versions) > 1:
        b.block(f"Agent version mismatch across hosts: "
                f"{ {v: len(h) for v, h in versions.items()} }")

    return {
        "versions_seen": {v: len(h) for v, h in versions.items()},
        "versionless_hosts": versionless,
        "total_online_hosts": len(hosts),
    }


# ── main ───────────────────────────────────────────────────────────────────

def run_preflight(db: Session) -> Tuple[_Blocker, Dict[str, Any]]:
    b = _Blocker()
    report: Dict[str, Any] = {}

    print("=== ADR-0020 Preflight ===")
    print(f"Time: {datetime.now(timezone.utc).isoformat()}")
    print()

    # 1. Workflow counts
    print("--- Workflow / TaskTemplate counts ---")
    report["counts"] = _check_workflow_counts(db, b)
    print(json.dumps(report["counts"], indent=2, default=str))
    print()

    # 2. Pipeline validation
    print("--- Pipeline definition validation ---")
    report["pipeline_issues"] = _validate_pipeline_defs(db, b)
    if report["pipeline_issues"]:
        print(f"  {len(report['pipeline_issues'])} issues found")
        for iss in report["pipeline_issues"][:20]:
            print(f"  - {iss}")
    else:
        print("  All clear")
    print()

    # 3. Script references
    print("--- Script references ---")
    report["script_ref_issues"] = _check_script_refs(db, b)
    if report["script_ref_issues"]:
        print(f"  {len(report['script_ref_issues'])} issues")
    else:
        print("  All clear")
    print()

    # 4. Default params conflicts
    print("--- Default params conflict pre-computation ---")
    report["default_params_conflicts"] = _check_default_params_conflicts(db, b)
    if report["default_params_conflicts"]:
        print(f"  {len(report['default_params_conflicts'])} conflicts")
    else:
        print("  All clear")
    print()

    # 5. Active jobs
    print("--- Active jobs (blocking) ---")
    report["active_jobs"] = _check_active_jobs(db, b)
    if report["active_jobs"]:
        print(f"  BLOCKED: {len(report['active_jobs'])} active jobs")
    else:
        print("  All clear")
    print()

    # 6. Orphan references
    print("--- Orphan references ---")
    report["orphans"] = _check_orphan_refs(db, b)
    if report["orphans"]:
        for tbl, rows in report["orphans"].items():
            print(f"  {tbl}: {len(rows)} orphans")
    else:
        print("  All clear")
    print()

    # 6b. ACTIVE leases on legacy script_execution jobs
    print("--- ACTIVE leases on legacy script_execution jobs ---")
    report["active_legacy_leases"] = _check_device_lease_active_legacy(db, b)
    if report["active_legacy_leases"]:
        print(f"  {len(report['active_legacy_leases'])} ACTIVE leases (Phase 5 will release)")
    else:
        print("  All clear")
    print()

    # 7. Redundant columns
    print("--- Redundant columns check ---")
    report["redundant_columns"] = _check_redundant_columns(db, b)
    print(json.dumps(report["redundant_columns"], indent=2))
    print()

    # 8. Script execution history (archive only)
    print("--- Script execution history (archive, not migrated) ---")
    report["script_execution_history"] = _check_script_execution_history(db, b)
    if report["script_execution_history"]:
        print(f"  {len(report['script_execution_history'])} rows")
    else:
        print("  None")
    print()

    # 9. Script sequences (archive only)
    print("--- Script sequences (archive, not migrated) ---")
    report["script_sequences"] = _check_script_sequences(db, b)
    if report["script_sequences"]:
        print(f"  {len(report['script_sequences'])} rows")
    else:
        print("  None")
    print()

    # 10. Agent version consistency
    print("--- Agent version consistency ---")
    report["agent_versions"] = _check_agent_version_consistency(db, b)
    print(json.dumps(report["agent_versions"], indent=2, default=str))
    print()

    # Summary
    print("=" * 60)
    if b.blocked:
        print(f"BLOCKED — {len(b.blocks)} blocking issues:")
        for msg in b.blocks:
            print(f"  [BLOCK] {msg}")
    else:
        print("PASSED — no blocking issues")
    if b.warnings:
        print(f"\n{len(b.warnings)} warning(s):")
        for msg in b.warnings:
            print(f"  [WARN]  {msg}")
    print("=" * 60)

    return b, report


def _write_artifacts(report: Dict[str, Any], csv_dir: Optional[str],
                     as_json: bool) -> None:
    if not csv_dir:
        return
    os.makedirs(csv_dir, exist_ok=True)

    # Per-category CSVs
    csv_sections = [
        ("multi_template_workflows",
         report.get("counts", {}).get("multi_template_workflows", [])),
        ("old_task_schedules",
         report.get("counts", {}).get("old_task_schedules", [])),
        ("pipeline_issues", report.get("pipeline_issues", [])),
        ("script_ref_issues", report.get("script_ref_issues", [])),
        ("default_params_conflicts", report.get("default_params_conflicts", [])),
        ("active_jobs", report.get("active_jobs", [])),
        ("script_execution_history", report.get("script_execution_history", [])),
        ("script_sequences", report.get("script_sequences", [])),
    ]
    for name, rows in csv_sections:
        if not rows:
            continue
        path = os.path.join(csv_dir, f"{name}.csv")
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)

    # Orphans
    orphans = report.get("orphans", {})
    for tbl, rows in orphans.items():
        if not rows:
            continue
        path = os.path.join(csv_dir, f"orphans_{tbl}.csv")
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)

    # JSON summary
    if as_json:
        json_path = os.path.join(csv_dir, "preflight_summary.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, default=str)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="ADR-0020 preflight validation")
    parser.add_argument("--json", action="store_true",
                        help="Write preflight_summary.json to --csv-dir")
    parser.add_argument("--csv-dir", type=str, default=None,
                        help="Directory for CSV/JSON output files")
    args = parser.parse_args()

    db = _build_session()
    try:
        b, report = run_preflight(db)
        _write_artifacts(report, args.csv_dir, args.json)
    finally:
        db.close()

    if b.blocked:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
