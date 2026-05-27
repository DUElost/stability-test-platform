"""Centralised job / lease timeout defaults (main-chain fragility §2.4–2.9).

Production defaults are unchanged. Override via env; dev-only shorter defaults
apply when ``ENV`` is set and not ``production`` (and ``TESTING!=1``).

| Variable | Production default | Dev default (optional) |
|----------|-------------------|------------------------|
| ``DISPATCHED_TIMEOUT_SECONDS`` / ``RUN_DISPATCHED_TIMEOUT_SECONDS`` | 120 | 120 |
| ``RUNNING_HEARTBEAT_TIMEOUT_SECONDS`` / ``RUN_HEARTBEAT_TIMEOUT_SECONDS`` | 900 | 900 |
| ``PATROL_RUNNING_HEARTBEAT_TIMEOUT_SECONDS`` | 300 | 180 |
| ``UNKNOWN_GRACE_SECONDS`` | 300 | 300 |
"""

from __future__ import annotations

from datetime import datetime, timezone
import os


def _is_non_production_runtime() -> bool:
    env = os.getenv("ENV", "").strip().lower()
    if not env or env == "production":
        return False
    if os.getenv("TESTING") == "1":
        return False
    return True


def _int_env(*names: str, production_default: int, dev_default: int | None = None) -> int:
    for name in names:
        raw = os.getenv(name)
        if raw is not None and raw.strip() != "":
            return int(raw)
    if _is_non_production_runtime() and dev_default is not None:
        return dev_default
    return production_default


# PENDING job never claimed by Agent → recycler marks FAILED
DISPATCHED_TIMEOUT_SECONDS = _int_env(
    "DISPATCHED_TIMEOUT_SECONDS",
    "RUN_DISPATCHED_TIMEOUT_SECONDS",
    production_default=120,
)

# RUNNING job lost heartbeat → UNKNOWN (lease stays; reconciler grace follows)
RUNNING_HEARTBEAT_TIMEOUT_SECONDS = _int_env(
    "RUNNING_HEARTBEAT_TIMEOUT_SECONDS",
    "RUN_HEARTBEAT_TIMEOUT_SECONDS",
    production_default=900,
)

# RUNNING patrol-phase jobs (pipeline has patrol + patrol signal present)
PATROL_RUNNING_HEARTBEAT_TIMEOUT_SECONDS = _int_env(
    "PATROL_RUNNING_HEARTBEAT_TIMEOUT_SECONDS",
    production_default=300,
    dev_default=180,
)

PATROL_STALL_MULTIPLIER = _int_env(
    "PATROL_STALL_MULTIPLIER",
    production_default=3,
)

# UNKNOWN job grace before lease release + FAILED
UNKNOWN_GRACE_SECONDS = _int_env(
    "UNKNOWN_GRACE_SECONDS",
    production_default=300,
)


def has_patrol_lifecycle(pipeline_def: object) -> bool:
    if not isinstance(pipeline_def, dict):
        return False
    patrol = (pipeline_def.get("lifecycle") or {}).get("patrol")
    return isinstance(patrol, dict)


def _aware_dt(value: object) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _patrol_interval_seconds(pipeline_def: object) -> int | None:
    if not isinstance(pipeline_def, dict):
        return None
    patrol = (pipeline_def.get("lifecycle") or {}).get("patrol")
    if not isinstance(patrol, dict):
        return None
    interval = patrol.get("interval_seconds")
    if isinstance(interval, int) and interval > 0:
        return interval
    return None


def job_in_patrol_phase(
    *,
    patrol_cycle_count: int | None,
    last_patrol_heartbeat_at: object,
    current_patrol_step: object,
) -> bool:
    return (
        (patrol_cycle_count or 0) > 0
        or last_patrol_heartbeat_at is not None
        or bool(current_patrol_step)
    )


def running_heartbeat_timeout_seconds(
    job: object,
    *,
    patrol_stall_multiplier: int | None = None,
) -> int:
    """Graded RUNNING timeout: patrol-active jobs may use a separate window."""
    pipeline_def = getattr(job, "pipeline_def", None)
    if has_patrol_lifecycle(pipeline_def) and job_in_patrol_phase(
        patrol_cycle_count=getattr(job, "patrol_cycle_count", None),
        last_patrol_heartbeat_at=getattr(job, "last_patrol_heartbeat_at", None),
        current_patrol_step=getattr(job, "current_patrol_step", None),
    ):
        interval = _patrol_interval_seconds(pipeline_def)
        multiplier = patrol_stall_multiplier or PATROL_STALL_MULTIPLIER
        timeout = PATROL_RUNNING_HEARTBEAT_TIMEOUT_SECONDS
        if interval is not None:
            timeout = max(timeout, interval * multiplier)

        updated_at = _aware_dt(getattr(job, "updated_at", None))
        next_retry_at = _aware_dt(getattr(job, "next_retry_at", None))
        if interval is not None and updated_at is not None and next_retry_at is not None:
            retry_delay = max(0, int((next_retry_at - updated_at).total_seconds()))
            timeout = max(timeout, retry_delay + interval * multiplier)
        return timeout
    return RUNNING_HEARTBEAT_TIMEOUT_SECONDS
