"""Read-only, low-frequency PostgreSQL growth sample for /metrics (#3327)."""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.core.metrics import (
    PROMETHEUS_AVAILABLE,
    db_growth_snapshot_timestamp_seconds,
    db_table_dead_rows_estimate,
    db_table_idx_scans,
    db_table_last_autoanalyze_timestamp_seconds,
    db_table_last_autovacuum_timestamp_seconds,
    db_table_live_rows_estimate,
    db_table_seq_scans,
    db_table_total_bytes,
)

logger = logging.getLogger(__name__)

_SAMPLE_INTERVAL_SECONDS = 300
_QUERY = text("""
    SELECT schemaname, relname, n_live_tup, n_dead_tup,
           pg_total_relation_size(relid) AS total_bytes,
           seq_scan, idx_scan, last_autovacuum, last_autoanalyze
    FROM pg_stat_user_tables
    WHERE schemaname NOT LIKE 'pg_temp%'
    ORDER BY schemaname, relname
""")
_METRICS = {
    "live": db_table_live_rows_estimate,
    "dead": db_table_dead_rows_estimate,
    "bytes": db_table_total_bytes,
    "seq": db_table_seq_scans,
    "idx": db_table_idx_scans,
    "vacuum": db_table_last_autovacuum_timestamp_seconds,
    "analyze": db_table_last_autoanalyze_timestamp_seconds,
}
_EXPOSED: dict[str, set[tuple[str, str]]] = {key: set() for key in _METRICS}
_LAST_SAMPLE_MONOTONIC: float | None = None
_LOCK = threading.Lock()


def refresh_db_growth_gauges(db: Session) -> None:
    """Sample catalog estimates at most once per five minutes per process.

    A failed read leaves the last sample timestamp unchanged, so consumers can
    distinguish a stale snapshot from stable table values. All SQL is read-only.
    """
    global _LAST_SAMPLE_MONOTONIC
    if not PROMETHEUS_AVAILABLE:
        return
    now = time.monotonic()
    if _LAST_SAMPLE_MONOTONIC is not None and now - _LAST_SAMPLE_MONOTONIC < _SAMPLE_INTERVAL_SECONDS:
        return
    with _LOCK:
        now = time.monotonic()
        if _LAST_SAMPLE_MONOTONIC is not None and now - _LAST_SAMPLE_MONOTONIC < _SAMPLE_INTERVAL_SECONDS:
            return
        try:
            rows = db.execute(_QUERY).mappings().all()
        except SQLAlchemyError:
            logger.warning("db_growth_metrics_refresh_failed", exc_info=True)
            return

        seen: dict[str, set[tuple[str, str]]] = {key: set() for key in _METRICS}
        for row in rows:
            labels = (str(row["schemaname"]), str(row["relname"]))
            db_table_live_rows_estimate.labels(*labels).set(row["n_live_tup"])
            db_table_dead_rows_estimate.labels(*labels).set(row["n_dead_tup"])
            db_table_total_bytes.labels(*labels).set(row["total_bytes"])
            seen["live"].add(labels)
            seen["dead"].add(labels)
            seen["bytes"].add(labels)
            if row["seq_scan"] is not None:
                db_table_seq_scans.labels(*labels).set(row["seq_scan"])
                seen["seq"].add(labels)
            if row["idx_scan"] is not None:
                db_table_idx_scans.labels(*labels).set(row["idx_scan"])
                seen["idx"].add(labels)
            if isinstance(row["last_autovacuum"], datetime):
                db_table_last_autovacuum_timestamp_seconds.labels(*labels).set(
                    row["last_autovacuum"].timestamp(),
                )
                seen["vacuum"].add(labels)
            if isinstance(row["last_autoanalyze"], datetime):
                db_table_last_autoanalyze_timestamp_seconds.labels(*labels).set(
                    row["last_autoanalyze"].timestamp(),
                )
                seen["analyze"].add(labels)
        for key, metric in _METRICS.items():
            for labels in _EXPOSED[key] - seen[key]:
                try:
                    metric.remove(*labels)
                except KeyError:
                    pass
            _EXPOSED[key] = seen[key]
        db_growth_snapshot_timestamp_seconds.set(datetime.now(timezone.utc).timestamp())
        _LAST_SAMPLE_MONOTONIC = now
