"""Device-return contract shared by Agent and control plane (ADR-0061 / #3723).

U1 owns machine values, FAMILY_STEPS, footprint helpers, lease-lost conflict
handling, and EXECUTING_DISARM. U5 (#3730) appends power-cycle ONBOARDED only.
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

# §3.0 第 2 / 6 条 / U5：已接入专项契约（本批仅 power-cycle）。
# verified_device_classes 与 powercycle_disarm._VERIFIED_DEVICE_CLASSES 必须同步；
# 真机未验证前保持空——clear 回读不得记 confirmed（ADR-0061 D2.5）。


def _pc_disarm_min_wall(params: Mapping[str, Any] | None) -> int:
    """disarm：wait_device_online_seconds + 60（#3723 §3.6 第 3 条）。"""
    p = params or {}
    wait = int(p.get("wait_device_online_seconds", 600) or 600)
    return wait + 60


def _pc_finish_min_wall(params: Mapping[str, Any] | None) -> int:
    """finish：collect_attempts × collect_retry_wait_seconds + 60（#3723 §3.6 第 3 条）。"""
    p = params or {}
    attempts = max(1, int(p.get("collect_attempts", 3) or 3))
    retry_wait = int(p.get("collect_retry_wait_seconds", 120) or 120)
    return attempts * retry_wait + 60


ONBOARDED: dict[str, Any] = {
    "power-cycle": {
        # 足迹清单（D2 第 1 条）
        "footprints": (
            "prefs:/data/data/com.tinno.autotesttool/shared_prefs/powercycle_runner.xml"
            " (running, auto_resume)",
            "service:com.tinno.autotesttool/"
            "com.mediatek.schpwronoff.powercycle.PowerCycleService",
        ),
        # 解除脚本与最低版本
        "disarm_script": "powercycle_disarm",
        "disarm_min_version": "1.0.2",
        "finish_script": "powercycle_finish",
        "finish_min_version": "1.1.0",
        # 探针模式（脚本参数 mode）
        "probe_modes": ("probe", "disarm"),
        # 已验证设备类别（device.platform + device.model）；空=尚未真机验证
        "verified_device_classes": [],
        # 解除预算秒数（默认参数下的墙钟下限，便于运维阅读）
        "disarm_budget_seconds": 660,
        "finish_budget_seconds": 420,
        # 墙钟下限（Plan API / Agent 消费；callable 吃有效参数）
        "min_wall_clock_seconds": {
            "powercycle_disarm": _pc_disarm_min_wall,
            "powercycle_finish": _pc_finish_min_wall,
        },
    },
}


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
    category_verified: Optional[bool] = None,
) -> str:
    """Map a teardown step outcome to one of the six D1.2 machine values.

    Priority: lease_lost → not_executed; incomplete → incomplete (even when
    the step has not started — SchedulerShutdown / permit-wait cut-off);
    skipped / not started → not_executed; structured_result (from onboarded
    script JSON) when valid; else success path is confirmed only for
    onboarded+readback ok, otherwise executed_unverified; failure → failed;
    readback_failed when marked.

    ``category_verified``（v1.6 §3.6.1 最小适配）：仅当显式 ``False`` 时，
    禁止把原始 readback_failed 提升为正式 readback_failed（成功路径 →
    executed_unverified；失败路径 → failed）。``None``/``True`` 保持原 U1 语义。
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
        if category_verified is False:
            if success is True:
                return DISARM_RESULT_EXECUTED_UNVERIFIED
            return DISARM_RESULT_FAILED
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
