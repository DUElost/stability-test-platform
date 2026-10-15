"""Device-return contract shared by Agent and control plane (ADR-0061 / #3723).

U1 owns this module: machine values, FAMILY_STEPS, footprint helpers, lease-lost
conflict handling, and EXECUTING_DISARM. ONBOARDED stays empty until U5.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, MutableMapping, Optional

# ── §3.0 第 1 条：机器值 ─────────────────────────────────────────────────────

DISARM_RESULT_CONFIRMED = "confirmed"
DISARM_RESULT_EXECUTED_UNVERIFIED = "executed_unverified"
DISARM_RESULT_FAILED = "failed"
DISARM_RESULT_READBACK_FAILED = "readback_failed"
DISARM_RESULT_NOT_EXECUTED = "not_executed"
DISARM_RESULT_INCOMPLETE = "incomplete"

DISARM_RESULTS: tuple[str, ...] = (
    DISARM_RESULT_CONFIRMED,
    DISARM_RESULT_EXECUTED_UNVERIFIED,
    DISARM_RESULT_FAILED,
    DISARM_RESULT_READBACK_FAILED,
    DISARM_RESULT_NOT_EXECUTED,
    DISARM_RESULT_INCOMPLETE,
)

FAMILY_RETURN_RETURNED = "returned"
FAMILY_RETURN_DEBT = "debt"
FAMILY_RETURN_UNVERIFIED = "unverified"

FAMILY_RETURN_STATES: tuple[str, ...] = (
    FAMILY_RETURN_RETURNED,
    FAMILY_RETURN_DEBT,
    FAMILY_RETURN_UNVERIFIED,
)

RETURN_SOURCE_JOB = "job"
RETURN_SOURCE_INIT_CHECK = "init_check"
RETURN_SOURCE_DISCHARGE = "discharge"
RETURN_SOURCE_OVERRIDE = "override"
RETURN_SOURCE_STOCK = "stock"

RETURN_SOURCES: tuple[str, ...] = (
    RETURN_SOURCE_JOB,
    RETURN_SOURCE_INIT_CHECK,
    RETURN_SOURCE_DISCHARGE,
    RETURN_SOURCE_OVERRIDE,
    RETURN_SOURCE_STOCK,
)

DEBT_STATE_OPEN = "open"
DEBT_STATE_CLOSED = "closed"

DEBT_STATES: tuple[str, ...] = (DEBT_STATE_OPEN, DEBT_STATE_CLOSED)

DEBT_CLOSE_DISCHARGED = "discharged"
DEBT_CLOSE_OVERRIDDEN = "overridden"

DEBT_CLOSE_REASONS: tuple[str, ...] = (
    DEBT_CLOSE_DISCHARGED,
    DEBT_CLOSE_OVERRIDDEN,
)

# ── §3.0 第 5 条：解除期间执行状态 ───────────────────────────────────────────

EXECUTING_DISARM = "EXECUTING_DISARM"

# Conflict reason when lease is already gone (§3.0 第 4 条 / D3 第 7 条)
LEASE_LOST_REASON = "lease_lost"

# ── §3.0 第 2 条：FAMILY_STEPS ───────────────────────────────────────────────
# disarm: steps used when the family is NOT in ONBOARDED (full family teardown).
# disarm_onboarded: steps used when the family IS in ONBOARDED (disarm only).

FAMILY_STEPS: dict[str, dict[str, tuple[str, ...]]] = {
    "power-cycle": {
        "footprint": ("powercycle_setup",),
        "disarm": ("powercycle_finish",),
        "disarm_onboarded": ("powercycle_disarm",),
    },
    "sleep": {
        "footprint": ("sleep_setup",),
        "disarm": ("sleep_finish",),
        "disarm_onboarded": ("sleep_finish",),
    },
    "mtbf": {
        "footprint": ("mtbf_setup",),
        "disarm": ("mtbf_finish",),
        "disarm_onboarded": ("mtbf_finish",),
    },
    "gpu": {
        "footprint": ("gpu_setup",),
        "disarm": ("gpu_finish",),
        "disarm_onboarded": ("gpu_finish",),
    },
    "monkey": {
        "footprint": ("monkey_setup", "monkey_launch", "monkey_test"),
        "disarm": ("monkey_teardown",),
        "disarm_onboarded": ("monkey_teardown",),
    },
}

# §3.0 第 6 条 / U5：已接入专项契约。U1 只定义空结构，不登记任何专项。
ONBOARDED: dict[str, Any] = {}


def script_name_from_step(step: Mapping[str, Any]) -> str:
    """Extract ``script:<name>`` leaf name from a PlanStep-like mapping."""
    action = step.get("action") or ""
    if not isinstance(action, str) or not action.startswith("script:"):
        return ""
    return action.split(":", 1)[1].strip()


def family_for_footprint_script(script_name: str) -> Optional[str]:
    """Return the family whose footprint list contains ``script_name``, if any."""
    if not script_name:
        return None
    for family, spec in FAMILY_STEPS.items():
        if script_name in spec["footprint"]:
            return family
    return None


def note_footprint_started(
    footprint_families: MutableMapping[str, bool] | set[str],
    step: Mapping[str, Any],
) -> Optional[str]:
    """Record a footprint when an init step matching FAMILY_STEPS starts.

    Conservative: footprint is established at *start* of execution, not completion
    (#3723 §3.0 第 3 条). Returns the family name when newly recorded, else None.
    """
    family = family_for_footprint_script(script_name_from_step(step))
    if family is None:
        return None
    if isinstance(footprint_families, set):
        if family in footprint_families:
            return None
        footprint_families.add(family)
        return family
    if footprint_families.get(family):
        return None
    footprint_families[family] = True
    return family


def disarm_script_names_for_family(
    family: str,
    *,
    onboarded: Mapping[str, Any] | None = None,
) -> tuple[str, ...]:
    """Disarm script names for ``family`` given current ONBOARDED registration."""
    spec = FAMILY_STEPS.get(family)
    if spec is None:
        return ()
    registry = ONBOARDED if onboarded is None else onboarded
    if family in registry:
        return spec["disarm_onboarded"]
    return spec["disarm"]


def is_disarm_script(
    script_name: str,
    *,
    footprint_families: Iterable[str],
    onboarded: Mapping[str, Any] | None = None,
) -> bool:
    """True when ``script_name`` is a disarm step for a family with footprint."""
    if not script_name:
        return False
    for family in footprint_families:
        if script_name in disarm_script_names_for_family(family, onboarded=onboarded):
            return True
    return False


def select_disarm_steps(
    teardown_def: Iterable[Mapping[str, Any]],
    footprint_families: Iterable[str],
    *,
    onboarded: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Filter teardown steps to disarm steps for families that left a footprint.

    Used for manual_exit (#3723 §3.2 第 3 条): onboarded families run only
    registered disarm steps; others run the family's full teardown step(s).
    """
    wanted: set[str] = set()
    for family in footprint_families:
        wanted.update(disarm_script_names_for_family(family, onboarded=onboarded))
    if not wanted:
        return []
    selected: list[dict[str, Any]] = []
    for step in teardown_def or []:
        if not isinstance(step, Mapping):
            continue
        if script_name_from_step(step) in wanted:
            selected.append(dict(step))
    return selected


def family_for_disarm_script(
    script_name: str,
    *,
    onboarded: Mapping[str, Any] | None = None,
) -> Optional[str]:
    """Inverse of disarm_script_names_for_family (first matching family)."""
    if not script_name:
        return None
    for family in FAMILY_STEPS:
        if script_name in disarm_script_names_for_family(family, onboarded=onboarded):
            return family
    return None


def make_disarm_result(
    *,
    result: str,
    step_id: str = "",
    exit_code: Optional[int] = None,
    readback_summary: str = "",
    reason: str = "",
) -> dict[str, Any]:
    """Build one family disarm_results entry (evidence + machine value)."""
    if result not in DISARM_RESULTS:
        raise ValueError(f"unknown disarm result: {result!r}")
    entry: dict[str, Any] = {
        "result": result,
        "step_id": step_id,
        "exit_code": exit_code,
        "readback_summary": readback_summary or "",
    }
    if reason:
        entry["reason"] = reason
    return entry


def map_step_outcome_to_disarm_result(
    *,
    started: bool,
    success: Optional[bool] = None,
    exit_code: Optional[int] = None,
    readback: Optional[str] = None,
    lease_lost: bool = False,
    incomplete: bool = False,
    skipped: bool = False,
    onboarded_family: bool = False,
    structured_result: Optional[str] = None,
) -> str:
    """Map a teardown step outcome to one of the six D1.2 machine values.

    Priority: lease_lost → not_executed; incomplete → incomplete (even when
    the step has not started — SchedulerShutdown / permit-wait cut-off);
    skipped / not started → not_executed; structured_result (from onboarded
    script JSON) when valid; else success path is confirmed only for
    onboarded+readback ok, otherwise executed_unverified; failure → failed;
    readback_failed when marked.
    """
    if lease_lost:
        return DISARM_RESULT_NOT_EXECUTED
    if incomplete:
        return DISARM_RESULT_INCOMPLETE
    if skipped or not started:
        return DISARM_RESULT_NOT_EXECUTED
    if structured_result in DISARM_RESULTS:
        return structured_result
    if readback == "readback_failed":
        return DISARM_RESULT_READBACK_FAILED
    if success is True:
        if onboarded_family and readback == "clear":
            return DISARM_RESULT_CONFIRMED
        return DISARM_RESULT_EXECUTED_UNVERIFIED
    if success is False:
        # SIGTERM/SIGKILL family often means platform interrupted the disarm.
        if exit_code in (143, -15, 137, -9):
            return DISARM_RESULT_INCOMPLETE
        return DISARM_RESULT_FAILED
    return DISARM_RESULT_NOT_EXECUTED


def default_not_executed_for_families(
    footprint_families: Iterable[str],
    *,
    reason: str = "",
) -> dict[str, dict[str, Any]]:
    """Seed disarm_results for every footprint family as not_executed."""
    out: dict[str, dict[str, Any]] = {}
    for family in footprint_families:
        out[family] = make_disarm_result(
            result=DISARM_RESULT_NOT_EXECUTED,
            reason=reason,
        )
    return out
