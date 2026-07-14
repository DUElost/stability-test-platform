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


# PENDING job never claimed by Agent → recycler marks FAILED.
# Does NOT apply while the same host still has RUNNING jobs (capacity queue).
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

# Patrol cadence stall threshold shared by recycler and API deadline projection.
PATROL_STALL_MULTIPLIER = _int_env(
    "PATROL_STALL_MULTIPLIER",
    production_default=3,
)

PRECHECK_QUEUE_STALE_SECONDS = _int_env(
    "PRECHECK_QUEUE_STALE_SECONDS",
    production_default=90,
)
PRECHECK_ACTIVE_STALE_SECONDS = _int_env(
    "PRECHECK_ACTIVE_STALE_SECONDS",
    production_default=180,
)

ABORT_ACK_GRACE_SECONDS = _int_env(
    "ABORT_REAPER_GRACE_SECONDS",
    production_default=60,
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


def running_heartbeat_timeout_seconds(job: object) -> int:
    """Graded RUNNING timeout: patrol-active jobs may use a separate window."""
    pipeline_def = getattr(job, "pipeline_def", None)
    if has_patrol_lifecycle(pipeline_def) and job_in_patrol_phase(
        patrol_cycle_count=getattr(job, "patrol_cycle_count", None),
        last_patrol_heartbeat_at=getattr(job, "last_patrol_heartbeat_at", None),
        current_patrol_step=getattr(job, "current_patrol_step", None),
    ):
        return PATROL_RUNNING_HEARTBEAT_TIMEOUT_SECONDS
    return RUNNING_HEARTBEAT_TIMEOUT_SECONDS
