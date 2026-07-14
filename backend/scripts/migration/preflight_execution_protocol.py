"""Execution protocol hardening — preflight before alembic upgrade.

Run BEFORE `alembic upgrade head` when applying revision
`c8d9e0f1a2b3_harden_execution_protocol_contracts`.

Checks:
  - duplicate (plan_run_id, device_id) in job_instance
  - duplicate active jobs per device (PENDING/RUNNING/UNKNOWN)
  - failure_threshold outside [0, 1] on plan / plan_run

Usage:
    python -m backend.scripts.migration.preflight_execution_protocol [--json]

Exit code:
    0 — safe to upgrade
    1 — blocking issues found (or DB error)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from backend.core.database import normalize_sync_database_url


def _get_db_url() -> str:
    url = os.getenv("DATABASE_URL", "")
    if not url:
        url = "postgresql://stability:stability@localhost:5432/stability"
    return normalize_sync_database_url(url)


def _build_session() -> Session:
    engine = create_engine(_get_db_url())
    return sessionmaker(bind=engine)()


_DUPLICATE_JOB_SQL = text(
    """
    SELECT plan_run_id,
           device_id,
           count(*) AS duplicates,
           array_agg(id ORDER BY id) AS job_ids
      FROM job_instance
     GROUP BY plan_run_id, device_id
    HAVING count(*) > 1
     ORDER BY plan_run_id, device_id
    """
)

_ACTIVE_DEVICE_DUP_SQL = text(
    """
    SELECT device_id,
           count(*) AS duplicates,
           array_agg(id ORDER BY id) AS job_ids
      FROM job_instance
     WHERE status IN ('PENDING', 'RUNNING', 'UNKNOWN')
     GROUP BY device_id
    HAVING count(*) > 1
     ORDER BY device_id
    """
)

_PLAN_THRESHOLD_SQL = text(
    """
    SELECT 'plan' AS table_name, id, failure_threshold
      FROM plan
     WHERE failure_threshold < 0.0 OR failure_threshold > 1.0
    UNION ALL
    SELECT 'plan_run' AS table_name, id, failure_threshold
      FROM plan_run
     WHERE failure_threshold < 0.0 OR failure_threshold > 1.0
     ORDER BY table_name, id
    """
)


def run_preflight() -> Dict[str, Any]:
    session = _build_session()
    try:
        dup_rows = session.execute(_DUPLICATE_JOB_SQL).mappings().all()
        active_rows = session.execute(_ACTIVE_DEVICE_DUP_SQL).mappings().all()
        threshold_rows = session.execute(_PLAN_THRESHOLD_SQL).mappings().all()

        duplicates = [dict(r) for r in dup_rows]
        active_device_duplicates = [dict(r) for r in active_rows]
        invalid_thresholds = [dict(r) for r in threshold_rows]

        blocking = (
            len(duplicates) > 0
            or len(active_device_duplicates) > 0
            or len(invalid_thresholds) > 0
        )
        return {
            "ok": not blocking,
            "duplicate_plan_run_device_groups": len(duplicates),
            "duplicates": duplicates,
            "active_device_duplicate_groups": len(active_device_duplicates),
            "active_device_duplicates": active_device_duplicates,
            "invalid_failure_threshold_rows": len(invalid_thresholds),
            "invalid_failure_thresholds": invalid_thresholds,
            "remediation": (
                "Resolve all listed issues before upgrade. "
                "Duplicates: keep canonical job rows per group. "
                "Active-device duplicates: leave at most one "
                "PENDING/RUNNING/UNKNOWN job per device. "
                "Thresholds: set failure_threshold to a value in [0, 1]."
            ),
        }
    finally:
        session.close()


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Preflight checks for execution-protocol migration c8d9e0f1a2b3",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON summary to stdout",
    )
    args = parser.parse_args(argv)

    try:
        report = run_preflight()
    except Exception as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        else:
            print(f"PREFLIGHT FAILED: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        if report["ok"]:
            print(
                "PREFLIGHT OK: no blocking duplicates or invalid failure_threshold"
            )
        else:
            if report["duplicate_plan_run_device_groups"]:
                print(
                    f"PREFLIGHT BLOCKED: {report['duplicate_plan_run_device_groups']} "
                    "duplicate (plan_run_id, device_id) group(s)",
                    file=sys.stderr,
                )
                for item in report["duplicates"]:
                    print(
                        f"  plan_run_id={item['plan_run_id']} device_id={item['device_id']} "
                        f"count={item['duplicates']} job_ids={item['job_ids']}",
                        file=sys.stderr,
                    )
            if report["active_device_duplicate_groups"]:
                print(
                    f"PREFLIGHT BLOCKED: {report['active_device_duplicate_groups']} "
                    "active-job-per-device duplicate group(s)",
                    file=sys.stderr,
                )
                for item in report["active_device_duplicates"]:
                    print(
                        f"  device_id={item['device_id']} count={item['duplicates']} "
                        f"job_ids={item['job_ids']}",
                        file=sys.stderr,
                    )
            if report["invalid_failure_threshold_rows"]:
                print(
                    f"PREFLIGHT BLOCKED: {report['invalid_failure_threshold_rows']} "
                    "failure_threshold row(s) outside [0, 1]",
                    file=sys.stderr,
                )
                for item in report["invalid_failure_thresholds"]:
                    print(
                        f"  {item['table_name']} id={item['id']} "
                        f"failure_threshold={item['failure_threshold']}",
                        file=sys.stderr,
                    )
            print(f"\nRemediation: {report['remediation']}", file=sys.stderr)

    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
