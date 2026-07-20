"""ADR-0026 confirmed v1 parameter defaults + invariant checks.

These values were provisional in the ADR 「待定清单」. They are now the
documented production defaults, validated by:

- code wiring (env defaults already match),
- relationship invariants below (must hold for any override),
- synthetic OperationScheduler contention sim (see tests).

Full 44→60→100 host gray remains an ops checklist item; changing a default
requires re-running the invariant suite and recording the rationale in
ADR-0026's revision log.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Adr0026ParamDefaults:
    # Admission pump / aging
    admission_pump_interval_seconds: int = 5
    admission_pump_batch: int = 5
    admission_retry_backoff_seconds: int = 30
    admission_aging_step_seconds: int = 1800  # +1 effective priority / 30 min
    admission_aging_max_boost: int = 5

    # Agent OperationScheduler
    max_concurrent_operations: int = 5

    # Lease extend batching
    lease_extend_batch_chunk: int = 100
    lease_extend_batch_max: int = 200

    # Three-signal / coordinator clocks
    coordinator_heartbeat_interval_seconds: int = 30
    coordinator_heartbeat_timeout_seconds: int = 300
    running_execution_heartbeat_timeout_seconds: int = 900
    patrol_execution_heartbeat_timeout_seconds: int = 300
    # Business progress (last_progress_at): observability only in v1 — no
    # independent recycler kill clock (avoids double-kill with execution hb).
    progress_independent_timeout: bool = False

    # Barrier / counters
    barrier_timeout_seconds: int = 600
    counter_reconcile_interval_seconds: int = 300


DEFAULTS = Adr0026ParamDefaults()


def aging_effective_priority(
    base_priority: int,
    waited_seconds: float,
    *,
    step_seconds: int = DEFAULTS.admission_aging_step_seconds,
    max_boost: int = DEFAULTS.admission_aging_max_boost,
) -> int:
    """Mirror admission_pump SQL: LEAST(floor(wait/step), max_boost)."""
    if step_seconds <= 0 or max_boost <= 0:
        return base_priority
    boost = min(int(waited_seconds // step_seconds), max_boost)
    return base_priority + boost


def validate_param_invariants(
    *,
    pump_interval: int = DEFAULTS.admission_pump_interval_seconds,
    aging_step: int = DEFAULTS.admission_aging_step_seconds,
    aging_max_boost: int = DEFAULTS.admission_aging_max_boost,
    retry_backoff: int = DEFAULTS.admission_retry_backoff_seconds,
    permit_cap: int = DEFAULTS.max_concurrent_operations,
    coord_interval: int = DEFAULTS.coordinator_heartbeat_interval_seconds,
    coord_timeout: int = DEFAULTS.coordinator_heartbeat_timeout_seconds,
    exec_timeout: int = DEFAULTS.running_execution_heartbeat_timeout_seconds,
    patrol_timeout: int = DEFAULTS.patrol_execution_heartbeat_timeout_seconds,
    barrier_timeout: int = DEFAULTS.barrier_timeout_seconds,
    lease_chunk: int = DEFAULTS.lease_extend_batch_chunk,
    lease_max: int = DEFAULTS.lease_extend_batch_max,
    reconcile_interval: int = DEFAULTS.counter_reconcile_interval_seconds,
) -> list[str]:
    """Return human-readable invariant violations (empty = ok)."""
    errors: list[str] = []

    if pump_interval < 1 or pump_interval > 30:
        errors.append(f"pump_interval={pump_interval} outside [1, 30]")
    if aging_step < pump_interval:
        errors.append("aging_step must be >= pump_interval")
    if aging_max_boost < 0:
        errors.append("aging_max_boost must be >= 0")
    if retry_backoff < pump_interval:
        errors.append("retry_backoff should be >= pump_interval to avoid spin")
    if permit_cap < 1 or permit_cap > 64:
        errors.append(f"permit_cap={permit_cap} outside [1, 64]")
    # Coordinator must survive ≥3 missed heartbeats before kill.
    if coord_timeout < 3 * coord_interval:
        errors.append(
            f"coord_timeout={coord_timeout} < 3×coord_interval={3 * coord_interval}"
        )
    if patrol_timeout > exec_timeout:
        errors.append("patrol exec timeout must be <= non-patrol exec timeout")
    if barrier_timeout < coord_timeout:
        errors.append(
            "barrier_timeout should be >= coord_timeout "
            "(host-level wait must outlive coordinator liveness window)"
        )
    if lease_chunk < 1 or lease_chunk > lease_max:
        errors.append("lease_chunk must be in [1, lease_max]")
    if lease_max > 1000:
        errors.append("lease_max > 1000 risks oversized extend-batch payloads")
    if reconcile_interval < 60:
        errors.append("reconcile_interval < 60s is too chatty for O(1) self-heal")

    return errors


def simulate_permit_contention(
    *,
    devices: int,
    permit_cap: int,
    step_duration_s: float,
    wall_s: float,
) -> dict[str, float]:
    """Closed-form estimate of queue wait under saturated patrol wake.

    Assumes every device wants the executor every ``step_duration_s`` and the
    scheduler runs at ``permit_cap`` steady state. Returns mean queue wait and
    util. Used to confirm permit=5 stays sane for typical 20–60 device hosts.
    """
    if permit_cap < 1 or devices < 1 or step_duration_s <= 0 or wall_s <= 0:
        raise ValueError("invalid simulation inputs")
    # Offer load: devices each need 1/step_duration slots per second.
    demand = devices / step_duration_s
    capacity = permit_cap / step_duration_s
    util = min(1.0, demand / capacity) if capacity else 1.0
    # M/D/c style rough wait: if under capacity, wait ≈ 0; else grows with
    # excess demand × step duration / cap.
    if demand <= capacity:
        mean_wait = 0.0
    else:
        excess = demand - capacity
        mean_wait = (excess * step_duration_s) / permit_cap
    return {
        "demand_ops_per_s": demand,
        "capacity_ops_per_s": capacity,
        "utilization": util,
        "mean_queue_wait_s": mean_wait,
        "wall_s": wall_s,
    }
