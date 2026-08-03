"""Clean stale execution_state on terminal jobs (#116 follow-up).

Jobs that reached COMPLETED/FAILED/ABORTED before 7c665ae (2026-08-03)
still carry a runtime sub-state (EXECUTING_STEP / WAITING_BARRIER /
PATROL_SLEEP / ...) that pollutes concurrency and observability queries
which do not filter by status (e.g. "host 并发看起来超 permit cap").

Idempotent: a second run reports ``scanned=0`` once the data is clean.

Usage:
    python -m backend.scripts.clean_terminal_execution_state --dry-run
    python -m backend.scripts.clean_terminal_execution_state

Run with the same environment as the backend (DATABASE_URL etc.).
"""

from __future__ import annotations

import argparse

from sqlalchemy import select, update

from backend.core.database import SessionLocal
from backend.models.enums import JobStatus
from backend.models.job import JobInstance

_TERMINAL_STATUSES = [
    JobStatus.COMPLETED.value,
    JobStatus.FAILED.value,
    JobStatus.ABORTED.value,
]


def clean_terminal_execution_state(
    db,
    *,
    dry_run: bool = False,
    limit: int | None = None,
) -> dict[str, object]:
    """Null ``execution_state`` on every terminal job that still has one.

    ``dry_run`` only reports; the real run is a single bounded UPDATE.
    Returns a summary dict for printing.
    """
    stmt = (
        select(JobInstance.id)
        .where(
            JobInstance.status.in_(_TERMINAL_STATUSES),
            JobInstance.execution_state.is_not(None),
        )
        .order_by(JobInstance.id)
    )
    if limit and limit > 0:
        stmt = stmt.limit(limit)
    ids = [row.id for row in db.execute(stmt).all()]
    summary: dict[str, object] = {
        "dry_run": dry_run,
        "scanned": len(ids),
        "changed": 0,
        "job_ids": ids,
    }
    if not ids:
        return summary
    if dry_run:
        summary["changed"] = len(ids)
        return summary

    updated = db.execute(
        update(JobInstance)
        .where(
            JobInstance.id.in_(ids),
            JobInstance.execution_state.is_not(None),
        )
        .values(execution_state=None)
    )
    db.commit()
    summary["changed"] = updated.rowcount
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report how many terminal jobs still carry execution_state, without changing them",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Maximum number of rows to scan (0 = no limit)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    with SessionLocal() as db:
        summary = clean_terminal_execution_state(
            db,
            dry_run=args.dry_run,
            limit=args.limit or None,
        )
    print(
        "terminal_execution_state_clean "
        f"dry_run={summary['dry_run']} scanned={summary['scanned']} "
        f"changed={summary['changed']}"
    )
    if summary["job_ids"]:
        print("job_ids:", ",".join(str(item) for item in summary["job_ids"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
