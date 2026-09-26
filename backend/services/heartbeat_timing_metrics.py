"""Mirror bounded Agent heartbeat timing histograms from Host.extra (#3219)."""

from __future__ import annotations

import logging
import math
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.agent.contracts.heartbeat_timing import (
    BUCKETS_SECONDS, PHASES, SNAPSHOT_VERSION,
)
from backend.core.metrics import (
    PROMETHEUS_AVAILABLE,
    agent_heartbeat_phase_bucket,
    agent_heartbeat_phase_count,
    agent_heartbeat_phase_sum,
    agent_heartbeat_probe_due,
)
from backend.core.job_timeout_config import HOST_HEARTBEAT_TIMEOUT_SECONDS
from backend.models.host import Host

logger = logging.getLogger(__name__)

_GAUGES = {
    "bucket": agent_heartbeat_phase_bucket,
    "count": agent_heartbeat_phase_count,
    "sum": agent_heartbeat_phase_sum,
    "due": agent_heartbeat_probe_due,
}
_EXPOSED: dict[str, set[tuple[str, ...]]] = {name: set() for name in _GAUGES}
_LOCK = threading.Lock()
_BUCKET_LABELS = tuple(str(bound) for bound in BUCKETS_SECONDS) + ("+Inf",)


def _valid_phase(raw: Any) -> tuple[list[int], int, float] | None:
    if not isinstance(raw, dict):
        return None
    buckets = raw.get("buckets")
    count = raw.get("count")
    total = raw.get("sum")
    if (
        not isinstance(buckets, list)
        or len(buckets) != len(_BUCKET_LABELS)
        or isinstance(count, bool)
        or not isinstance(count, int)
        or count < 0
        or isinstance(total, bool)
        or not isinstance(total, (int, float))
        or not math.isfinite(total)
        or total < 0
    ):
        return None
    previous = 0
    for value in buckets:
        if isinstance(value, bool) or not isinstance(value, int) or value < previous:
            return None
        previous = value
    if buckets[-1] != count:
        return None
    return buckets, count, float(total)


def refresh_agent_heartbeat_timing_gauges(db: Session) -> None:
    """Refresh live host series and remove retired/rolled-back Agent children.

    No snapshot means unknown, not a zero-duration tick. A failed DB read keeps
    prior values until the next scrape rather than clearing all hosts at once.
    """
    if not PROMETHEUS_AVAILABLE:
        return
    try:
        fresh_since = datetime.now(timezone.utc) - timedelta(
            seconds=HOST_HEARTBEAT_TIMEOUT_SECONDS,
        )
        rows = db.query(Host.id, Host.extra).filter(
            Host.retired_at.is_(None), Host.last_heartbeat >= fresh_since,
        ).all()
    except SQLAlchemyError:
        # #3102 同款：共享 session 读失败必须 rollback，否则同一次 scrape 的后续各组连环失败。
        db.rollback()
        logger.warning("heartbeat_timing_metrics_refresh_failed", exc_info=True)
        return
    seen: dict[str, set[tuple[str, ...]]] = {name: set() for name in _GAUGES}
    with _LOCK:
        for host_id, extra in rows:
            if not isinstance(extra, dict):
                continue
            snapshot = extra.get("heartbeat_timing")
            if not isinstance(snapshot, dict) or snapshot.get("version") != SNAPSHOT_VERSION:
                continue
            phases = snapshot.get("phases")
            if not isinstance(phases, dict):
                continue
            hid = str(host_id)
            for phase in PHASES:
                parsed = _valid_phase(phases.get(phase))
                if parsed is None:
                    continue
                buckets, count, total = parsed
                for le, value in zip(_BUCKET_LABELS, buckets, strict=True):
                    labels = (hid, phase, le)
                    agent_heartbeat_phase_bucket.labels(*labels).set(value)
                    seen["bucket"].add(labels)
                labels_phase = (hid, phase)
                agent_heartbeat_phase_count.labels(*labels_phase).set(count)
                agent_heartbeat_phase_sum.labels(*labels_phase).set(total)
                seen["count"].add(labels_phase)
                seen["sum"].add(labels_phase)
            for kind, key in (("slow", "slow_due"), ("disk", "disk_due")):
                due = snapshot.get(key)
                if isinstance(due, int) and not isinstance(due, bool) and due >= 0:
                    labels_due = (hid, kind)
                    agent_heartbeat_probe_due.labels(*labels_due).set(due)
                    seen["due"].add(labels_due)
        for name, gauge in _GAUGES.items():
            for labels in _EXPOSED[name] - seen[name]:
                try:
                    gauge.remove(*labels)
                except KeyError:
                    pass
            _EXPOSED[name] = seen[name]
