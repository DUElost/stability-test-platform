"""Log observation layer — ADR-0028 authority vs UI aggregation (#519).

``device_log_event`` is the upload/extract authority; ``job_log_signal`` remains
the PlanRun watcher-summary transport. Risk rating merges both without
double-counting linked rows (signals with ``device_log_event_id`` are excluded).
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.services.report_service import (
    _DEFAULT_RISK_LEVEL,
    _RISK_SEVERITY_ORDER,
    _classify_subtype,
)

_DLE_RISK_EVENT_TYPES = ("AEE", "VENDOR_AEE", "ANR", "CRASH")
_SIGNAL_RISK_CATEGORIES = ("AEE", "VENDOR_AEE", "ANR")


def _rows_from_device_log_events(db: Session, job_ids: list[int]) -> list[tuple[str, int]]:
    sql = text("""
        SELECT
            COALESCE(NULLIF(event_subtype, ''), event_type) AS subtype,
            COUNT(DISTINCT COALESCE(remote_path, local_path)) AS dedup_count
        FROM device_log_event
        WHERE job_id = ANY(:job_ids)
          AND upper(event_type) = ANY(:event_types)
        GROUP BY subtype
    """)
    rows = db.execute(
        sql,
        {
            "job_ids": list(job_ids),
            "event_types": [t.upper() for t in _DLE_RISK_EVENT_TYPES],
        },
    ).all()
    return [(str(subtype), int(dedup_count)) for subtype, dedup_count in rows]


def _rows_from_unlinked_signals(db: Session, job_ids: list[int]) -> list[tuple[str, int]]:
    sql = text("""
        SELECT
            COALESCE(extra->>'event_subtype', category) AS subtype,
            COUNT(DISTINCT extra->>'nfs_path') AS dedup_count
        FROM job_log_signal
        WHERE job_id = ANY(:job_ids)
          AND device_log_event_id IS NULL
          AND category = ANY(:categories)
        GROUP BY subtype
    """)
    rows = db.execute(
        sql,
        {
            "job_ids": list(job_ids),
            "categories": list(_SIGNAL_RISK_CATEGORIES),
        },
    ).all()
    return [(str(subtype), int(dedup_count)) for subtype, dedup_count in rows]


def _build_risk_summary(subtype_counts: dict[str, int]) -> Optional[Dict[str, Any]]:
    if not subtype_counts:
        return None

    by_type: Dict[str, int] = {}
    by_severity: Dict[str, int] = {"S": 0, "A": 0, "B": 0}
    events_total = 0
    aee_entries = 0
    worst_level = _DEFAULT_RISK_LEVEL

    for subtype, count in subtype_counts.items():
        by_type[subtype] = count
        events_total += count
        upper = subtype.upper()
        if upper in ("AEE", "VENDOR_AEE", "CRASH"):
            aee_entries += count
        level = _classify_subtype(subtype, count)
        by_severity[level] = by_severity.get(level, 0) + 1
        if _RISK_SEVERITY_ORDER.get(level, 0) > _RISK_SEVERITY_ORDER.get(worst_level, 0):
            worst_level = level

    return {
        "risk_level": worst_level,
        "counts": {
            "by_type": by_type,
            "by_severity": by_severity,
            "events_total": events_total,
            "aee_entries": aee_entries,
        },
    }


def aggregate_risk_summary(db: Session, job_ids: list[int]) -> Optional[Dict[str, Any]]:
    """PlanRun-scoped risk rollup: DLE authority + legacy unlinked signals."""
    if not job_ids:
        return None

    merged: dict[str, int] = {}
    for subtype, count in _rows_from_device_log_events(db, job_ids):
        merged[subtype] = merged.get(subtype, 0) + count
    for subtype, count in _rows_from_unlinked_signals(db, job_ids):
        merged[subtype] = merged.get(subtype, 0) + count

    return _build_risk_summary(merged)


def aggregate_risk_summary_from_signals(
    db: Session, job_ids: list[int]
) -> Optional[Dict[str, Any]]:
    """Backward-compatible alias — prefer :func:`aggregate_risk_summary`."""
    return aggregate_risk_summary(db, job_ids)
