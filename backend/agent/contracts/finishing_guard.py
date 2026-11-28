"""Unified FinishingGuard contract (ADR-0065 / B12 U1).

Shared by Agent and control plane. Two orthogonal kinds share one bounded
protection base:

- ``teardown_disarm`` — B6 disarm / EXECUTING_DISARM (line-compatible)
- ``atomic_commit`` — irreversible hardware critical section (handshake in U4/U5)

Persistence stays ``RUNNING``; ``finishing`` is a read projection (U2).

Transition dual-column authority (#4206 v1.1)：A 类并存时任一列
``clock_regressed`` 即不可逆吸收失效；否则按单调 ``seq`` / 新鲜度取最新有效收据。
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Mapping

from .device_return import (
    DISARM_EVIDENCE_FRESH_SECONDS,
    DISARM_GUARD_PROTOCOL,
    disarm_guard_deadline,
    disarm_guard_expiry,
)

# ── kinds / triggers / guard states ─────────────────────────────────────────

FINISHING_KIND_TEARDOWN_DISARM = "teardown_disarm"
FINISHING_KIND_ATOMIC_COMMIT = "atomic_commit"

FINISHING_KINDS: tuple[str, ...] = (
    FINISHING_KIND_TEARDOWN_DISARM,
    FINISHING_KIND_ATOMIC_COMMIT,
)

FINISHING_TRIGGER_MANUAL_ABORT = "manual_abort"
FINISHING_TRIGGER_MANUAL_EXIT = "manual_exit"
FINISHING_TRIGGER_NATURAL_END = "natural_end"
FINISHING_TRIGGER_BODY_FAILURE = "body_failure"
FINISHING_TRIGGER_BARRIER_TIMEOUT = "barrier_timeout"

FINISHING_TRIGGERS: tuple[str, ...] = (
    FINISHING_TRIGGER_MANUAL_ABORT,
    FINISHING_TRIGGER_MANUAL_EXIT,
    FINISHING_TRIGGER_NATURAL_END,
    FINISHING_TRIGGER_BODY_FAILURE,
    FINISHING_TRIGGER_BARRIER_TIMEOUT,
)

GUARD_STATE_PRE_REGISTERED = "pre_registered"
GUARD_STATE_GRANTED = "granted"

GUARD_STATES: tuple[str, ...] = (
    GUARD_STATE_PRE_REGISTERED,
    GUARD_STATE_GRANTED,
)

GUARD_INVALIDATED_CLOCK_REGRESSED = "clock_regressed"

# Atomic commit budget defaults (ADR-0065 D4); wired by U4/U5 handshake.
ATOMIC_HARD_CAP_SECONDS = 3600
ATOMIC_DEFAULT_STEP_TIMEOUT_SECONDS = 1800
PRE_REG_GRACE_SECONDS = 30
ATOMIC_LIVENESS_FRESH_SECONDS = 60
ATOMIC_SILENT_WRITE_PROGRESS_WINDOW_SECONDS = 300

# Abort intensity (ADR-0065 D2 / D4.5 multi-subject merge). U4 wires force=true.
ABORT_INTENSITY_GRACEFUL = "graceful"
ABORT_INTENSITY_FORCE = "force"

_ABORT_INTENSITY_RANK = {
    ABORT_INTENSITY_GRACEFUL: 0,
    ABORT_INTENSITY_FORCE: 1,
}


def finishing_kind_of(guard: Mapping[str, Any] | None) -> str | None:
    """Return kind; legacy B6 ``disarm_guard`` (no kind) ⇒ teardown_disarm."""
    if not isinstance(guard, Mapping):
        return None
    kind = guard.get("kind")
    if kind in FINISHING_KINDS:
        return str(kind)
    # B6 wire payload: protocol + t0 / job_deadline without kind.
    if guard.get("protocol") == DISARM_GUARD_PROTOCOL or "job_deadline" in guard:
        return FINISHING_KIND_TEARDOWN_DISARM
    return None


def is_clock_regressed(guard: Mapping[str, Any] | None) -> bool:
    """Recognize both canonical and legacy B6 regression marks."""
    if not isinstance(guard, Mapping):
        return False
    if guard.get("guard_invalidated_reason") == GUARD_INVALIDATED_CLOCK_REGRESSED:
        return True
    return guard.get("reason") == GUARD_INVALIDATED_CLOCK_REGRESSED


def mark_clock_regressed(guard: Mapping[str, Any]) -> dict[str, Any]:
    """Unidirectional invalidate; never clears once set."""
    out = dict(guard)
    out["reason"] = GUARD_INVALIDATED_CLOCK_REGRESSED
    out["guard_invalidated_reason"] = GUARD_INVALIDATED_CLOCK_REGRESSED
    return out


def as_teardown_disarm_wire(guard: Mapping[str, Any]) -> dict[str, Any]:
    """Strip finishing envelope fields for B6 ``disarm_guard_*`` evaluators.

    Keep ``reason`` so legacy B6 expiry sees ``clock_regressed``; canonical
    ``guard_invalidated_reason`` is handled by :func:`is_clock_regressed`
    before calling the B6 helpers.
    """
    out = {k: v for k, v in guard.items() if k not in {
        "kind", "trigger", "guard_state", "guard_invalidated_reason",
        "permit_id", "request_id", "permit_expires_at", "D_atomic",
        "last_liveness_receipt_at", "last_progress_receipt_at",
        "silent_write_progress_window_seconds", "atomic_group_frozen",
        "negotiated_capabilities",
    }}
    return out


def wrap_disarm_as_finishing_guard(
    disarm_guard: Mapping[str, Any],
    *,
    trigger: str | None = None,
) -> dict[str, Any]:
    """Absorb a B6 disarm_guard payload as FinishingGuard(kind=teardown_disarm)."""
    out = deepcopy(dict(disarm_guard))
    out["kind"] = FINISHING_KIND_TEARDOWN_DISARM
    if trigger in FINISHING_TRIGGERS:
        out["trigger"] = trigger
    if "seq" not in out:
        out["seq"] = out.get("seq", -1)
    return out


def _as_teardown_view(guard: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(guard)
    if "kind" not in out:
        out["kind"] = FINISHING_KIND_TEARDOWN_DISARM
    return out


def _guard_seq(guard: Mapping[str, Any]) -> int:
    seq = guard.get("seq")
    return seq if type(seq) is int else -1


def _guard_freshness_stamp(guard: Mapping[str, Any]) -> float:
    """Monotonic-ish freshness for dual-column A-class selection."""
    for key in (
        "last_server_at",
        "coordinator_seen_at",
        "progress_seen_at",
        "queue_seen_at",
        "first_server_at",
        "t0",
    ):
        value = guard.get(key)
        if type(value) in (int, float):
            return float(value)
    return float(_guard_seq(guard))


def coerce_job_finishing_guard(
    finishing_guard: object,
    disarm_guard: object,
) -> dict[str, Any] | None:
    """Resolve dual-column A-class authority (#4206 v1.1).

    - Either column ``clock_regressed`` ⇒ absorbing invalidate (union).
    - Else pick the freshest A-class receipt by ``seq`` then freshness stamp
      (live B6 ``disarm_guard`` must beat a stale ``finishing_guard`` copy).
    - ``atomic_commit`` only lives on ``finishing_guard``.
    """
    finishing = finishing_guard if isinstance(finishing_guard, Mapping) else None
    legacy = disarm_guard if isinstance(disarm_guard, Mapping) else None

    finishing_kind = finishing_kind_of(finishing) if finishing is not None else None
    legacy_kind = finishing_kind_of(legacy) if legacy is not None else None

    # Atomic commit is finishing-column only.
    if finishing_kind == FINISHING_KIND_ATOMIC_COMMIT:
        out = dict(finishing)
        out.setdefault("kind", FINISHING_KIND_ATOMIC_COMMIT)
        return out

    teardown_candidates: list[dict[str, Any]] = []
    if finishing is not None and finishing_kind == FINISHING_KIND_TEARDOWN_DISARM:
        teardown_candidates.append(_as_teardown_view(finishing))
    if legacy is not None and (
        legacy_kind == FINISHING_KIND_TEARDOWN_DISARM or legacy_kind is None
    ):
        # Bare B6 wire without kind still counts as teardown.
        teardown_candidates.append(wrap_disarm_as_finishing_guard(legacy))

    if not teardown_candidates:
        return None

    # Absorbing fail: any column regressed ⇒ whole A-class guard is dead.
    if any(is_clock_regressed(c) for c in teardown_candidates):
        base = max(
            teardown_candidates,
            key=lambda g: (_guard_seq(g), _guard_freshness_stamp(g)),
        )
        return mark_clock_regressed(base)

    return max(
        teardown_candidates,
        key=lambda g: (_guard_seq(g), _guard_freshness_stamp(g)),
    )


def normalize_atomic_step_timeout(raw: object) -> int:
    """Missing / non-positive timeout → 1800s; cap at ATOMIC_HARD_CAP_SECONDS."""
    if type(raw) is int and raw > 0:
        return min(raw, ATOMIC_HARD_CAP_SECONDS)
    return ATOMIC_DEFAULT_STEP_TIMEOUT_SECONDS


def abort_intensity_of(*, force: bool) -> str:
    return ABORT_INTENSITY_FORCE if force else ABORT_INTENSITY_GRACEFUL


def max_abort_intensity(*intensities: str | bool | None) -> str:
    """Multi-subject intensity: ``force > graceful`` (ADR-0065 D2.10)."""
    best = ABORT_INTENSITY_GRACEFUL
    best_rank = _ABORT_INTENSITY_RANK[best]
    for item in intensities:
        if item is None:
            continue
        if isinstance(item, bool):
            name = abort_intensity_of(force=item)
        elif isinstance(item, str) and item in _ABORT_INTENSITY_RANK:
            name = item
        else:
            continue
        rank = _ABORT_INTENSITY_RANK[name]
        if rank > best_rank:
            best, best_rank = name, rank
    return best


def _aware(dt: datetime) -> datetime:
    """Normalize naive datetimes to UTC-aware (B7); reject mixed compare crashes."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def min_abort_clock(*clocks: datetime | None) -> datetime | None:
    """Multi-subject clock merge: earliest clock; naive treated as UTC."""
    present = [_aware(c) for c in clocks if isinstance(c, datetime)]
    if not present:
        return None
    return min(present)


def abort_request_covers_job(run_context: object, job_id: int) -> bool:
    """Same coverage predicate as ``job_timeout_config.abort_request_covers_job``.

    Kept in the shared contract so intensity max and clock min share one rule
    without pulling control-plane modules into Agent contracts.
    """
    ctx = run_context if isinstance(run_context, dict) else {}
    abort = ctx.get("abort_requested")
    if not isinstance(abort, dict):
        return True
    requested = abort.get("requested_job_ids")
    if not isinstance(requested, list) or not requested:
        return True
    try:
        return int(job_id) in {int(x) for x in requested}
    except (TypeError, ValueError):
        return True


def subject_force_flags(
    run_context: object,
    job_id: int,
    host_id: str | None = None,
) -> tuple[bool, bool]:
    """Return ``(run_force, host_force)`` only for subjects that cover this job."""
    ctx = run_context if isinstance(run_context, dict) else {}
    run_force = False
    if abort_request_covers_job(ctx, job_id):
        abort = ctx.get("abort_requested")
        if isinstance(abort, dict):
            run_force = bool(abort.get("force"))
    host_force = False
    if host_id:
        hosts = ctx.get("abort_requested_hosts")
        if isinstance(hosts, dict):
            clock = hosts.get(host_id)
            if clock is None:
                clock = hosts.get(str(host_id))
            if isinstance(clock, dict):
                host_force = bool(clock.get("force"))
    return run_force, host_force


def effective_abort_force(
    run_context: object,
    job_id: int,
    host_id: str | None = None,
) -> bool:
    """Highest force across subjects that cover this job (same set as clock min)."""
    run_force, host_force = subject_force_flags(run_context, job_id, host_id)
    return max_abort_intensity(run_force, host_force) == ABORT_INTENSITY_FORCE


def _atomic_commit_deadline(
    guard: Mapping[str, Any],
    *,
    now: float,
) -> float | None:
    """Bounded atomic_commit protection; U4 fills handshake writers."""
    if is_clock_regressed(guard):
        return None
    if now < float(guard.get("last_server_at", guard.get("last_liveness_receipt_at", now)) or now):
        return None
    d_atomic = guard.get("D_atomic")
    if type(d_atomic) not in (int, float):
        return None
    d_atomic_f = float(d_atomic)
    if now >= d_atomic_f:
        return None
    state = guard.get("guard_state")
    if state == GUARD_STATE_PRE_REGISTERED:
        permit_exp = guard.get("permit_expires_at")
        if type(permit_exp) not in (int, float):
            return None
        deadline = min(d_atomic_f, float(permit_exp))
        return deadline if now < deadline else None
    if state != GUARD_STATE_GRANTED:
        return None
    liveness_at = guard.get("last_liveness_receipt_at")
    if type(liveness_at) not in (int, float):
        return None
    liveness_deadline = float(liveness_at) + ATOMIC_LIVENESS_FRESH_SECONDS
    if now >= liveness_deadline:
        return None
    progress_at = guard.get("last_progress_receipt_at", liveness_at)
    if type(progress_at) not in (int, float):
        return None
    progress_window = guard.get("silent_write_progress_window_seconds")
    if type(progress_window) not in (int, float) or progress_window <= 0:
        progress_window = ATOMIC_SILENT_WRITE_PROGRESS_WINDOW_SECONDS
    progress_deadline = float(progress_at) + float(progress_window)
    progress_deadline = min(progress_deadline, d_atomic_f)
    if now >= progress_deadline:
        return None
    return min(d_atomic_f, liveness_deadline, progress_deadline)


def finishing_guard_deadline(
    guard: Mapping[str, Any] | None,
    *,
    now: float,
    owner_valid: bool,
    execution_heartbeat_at: float | None,
    execution_heartbeat_timeout_seconds: int = 60,
) -> float | None:
    """Unified protection predicate for reaper / recycler / projection."""
    if not guard or not owner_valid or is_clock_regressed(guard):
        return None
    kind = finishing_kind_of(guard)
    if kind == FINISHING_KIND_TEARDOWN_DISARM:
        return disarm_guard_deadline(
            as_teardown_disarm_wire(guard),
            now=now,
            owner_valid=owner_valid,
            execution_heartbeat_at=execution_heartbeat_at,
            execution_heartbeat_timeout_seconds=execution_heartbeat_timeout_seconds,
        )
    if kind == FINISHING_KIND_ATOMIC_COMMIT:
        return _atomic_commit_deadline(guard, now=now)
    return None


def finishing_guard_expiry(
    guard: Mapping[str, Any],
    *,
    execution_heartbeat_deadline: float | None = None,
) -> float:
    """Absolute next applicable limit for candidate selection.

    Canonical ``guard_invalidated_reason="clock_regressed"`` and legacy B6
    ``reason="clock_regressed"`` both collapse to ``t0`` (B2).
    """
    if is_clock_regressed(guard):
        return float(guard.get("t0", guard.get("last_server_at", 0)) or 0)
    kind = finishing_kind_of(guard)
    if kind == FINISHING_KIND_ATOMIC_COMMIT:
        d_atomic = guard.get("D_atomic")
        deadline = float(d_atomic) if type(d_atomic) in (int, float) else float(
            guard.get("last_liveness_receipt_at", 0) or 0
        )
        state = guard.get("guard_state")
        if state == GUARD_STATE_PRE_REGISTERED:
            permit_exp = guard.get("permit_expires_at")
            if type(permit_exp) in (int, float):
                deadline = min(deadline, float(permit_exp))
        else:
            liveness_at = guard.get("last_liveness_receipt_at")
            if type(liveness_at) in (int, float):
                deadline = min(
                    deadline, float(liveness_at) + ATOMIC_LIVENESS_FRESH_SECONDS,
                )
            progress_at = guard.get("last_progress_receipt_at", liveness_at)
            window = guard.get("silent_write_progress_window_seconds")
            if type(window) not in (int, float) or window <= 0:
                window = ATOMIC_SILENT_WRITE_PROGRESS_WINDOW_SECONDS
            if type(progress_at) in (int, float):
                deadline = min(deadline, float(progress_at) + float(window))
        return deadline
    # teardown_disarm (+ legacy)
    return disarm_guard_expiry(
        as_teardown_disarm_wire(guard),
        execution_heartbeat_deadline=execution_heartbeat_deadline,
    )


# Re-export B6 freshness constant for shared callers.
FINISHING_EVIDENCE_FRESH_SECONDS = DISARM_EVIDENCE_FRESH_SECONDS
