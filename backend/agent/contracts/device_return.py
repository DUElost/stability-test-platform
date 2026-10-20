"""Device-return contract shared by Agent and control plane (ADR-0061 / #3723).

U1 owns machine values, FAMILY_STEPS, footprint helpers, lease-lost conflict
handling, and EXECUTING_DISARM. U5 (#3730) appends power-cycle ONBOARDED,
job-level step-role helpers (§3.0.2 / §3.6.2), and Plan save-shape checks (§3.6.5).
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
# verified_device_classes 仅在本共享契约登记（U8 可信组合政策）；探针包不得内置副本
# （#3723 v1.7.1 §3.6.1.7）。真机未验证前保持空。


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
        "disarm_min_version": "1.0.7",
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



# ── §3.0.2 / §3.6.2：job 级步骤角色（按实际 version / 冻结包 SHA）────────────

STEP_ROLE_DISARM = "disarm"
STEP_ROLE_COLLECT = "collect"
STEP_ROLE_UNKNOWN_LEGACY_DISARM = "unknown_legacy_disarm"

REASON_UNKNOWN_LEGACY_VERSION = "unknown_legacy_version"

# Plan 保存入口 §3.6.5 reasons
TEARDOWN_REASON_DISARM_MISSING = "disarm_missing"
TEARDOWN_REASON_DISARM_DISABLED = "disarm_disabled"
TEARDOWN_REASON_DISARM_BELOW_MIN = "disarm_below_min_version"
TEARDOWN_REASON_FINISH_BELOW_MIN = "finish_below_min_version"

ONBOARDED_TEARDOWN_INVALID = "ONBOARDED_TEARDOWN_INVALID"


def version_key(version: str | None) -> tuple[int, ...]:
    """Numeric-segment version key (``1.0.10`` > ``1.0.3``); empty → ()."""
    if not version or not isinstance(version, str):
        return ()
    parts: list[int] = []
    for seg in version.strip().split("."):
        if not seg:
            return ()
        if not seg.isdigit():
            # non-numeric → unparsable
            return ()
        parts.append(int(seg))
    return tuple(parts)


def version_at_least(version: str | None, minimum: str) -> bool:
    """True when ``version`` parses and is >= ``minimum`` by numeric segments."""
    vk = version_key(version)
    mk = version_key(minimum)
    if not vk or not mk:
        return False
    # pad shorter
    n = max(len(vk), len(mk))
    vk = vk + (0,) * (n - len(vk))
    mk = mk + (0,) * (n - len(mk))
    return vk >= mk


def step_version_of(step: Mapping[str, Any]) -> str:
    """PlanStep uses ``script_version``; pipeline steps use ``version``."""
    for key in ("script_version", "version"):
        raw = step.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return ""


def _finish_major_minor(version: str) -> tuple[int, int] | None:
    vk = version_key(version)
    if len(vk) < 2:
        return None
    return int(vk[0]), int(vk[1])


def step_teardown_role(
    script_name: str,
    version: str | None = None,
    *,
    package_sha: str | None = None,
    onboarded: Mapping[str, Any] | None = None,
) -> Optional[str]:
    """Classify a teardown script by actual job version (#3723 §3.0.2).

    Returns one of:
    - ``disarm``: known disarm obligation (disarm any ver; finish@1.0.x)
    - ``collect``: finish@1.1.0+ collection only
    - ``unknown_legacy_disarm``: missing/unparsable version — conservative disarm
    - ``None``: not a power-cycle teardown script we classify

    ``package_sha`` is accepted for shared-call signature parity (U8 freezes it);
    role does not currently branch on SHA — identity is (name, version) in manifest.
    """
    del package_sha  # reserved for callers that pass frozen SHA
    if not script_name:
        return None
    _ = onboarded  # API parity with shared callers; role table is version-driven
    ver = (version or "").strip()

    if script_name == "powercycle_disarm":
        if not ver or not version_key(ver):
            return STEP_ROLE_UNKNOWN_LEGACY_DISARM
        return STEP_ROLE_DISARM

    if script_name == "powercycle_finish":
        if not ver or not version_key(ver):
            return STEP_ROLE_UNKNOWN_LEGACY_DISARM
        mm = _finish_major_minor(ver)
        if mm is None:
            return STEP_ROLE_UNKNOWN_LEGACY_DISARM
        major, minor = mm
        if (major, minor) == (1, 0):
            return STEP_ROLE_DISARM
        if (major, minor) >= (1, 1):
            return STEP_ROLE_COLLECT
        # 0.x or other — conservative
        return STEP_ROLE_UNKNOWN_LEGACY_DISARM

    # Non-power-cycle: fall back to FAMILY_STEPS registry lists (no version split).
    for _family, spec in FAMILY_STEPS.items():
        if script_name in spec["disarm"] or script_name in spec["disarm_onboarded"]:
            return STEP_ROLE_DISARM
    return None


def is_disarm_role(role: str | None) -> bool:
    return role in (STEP_ROLE_DISARM, STEP_ROLE_UNKNOWN_LEGACY_DISARM)


def is_disarm_script(
    script_name: str,
    *,
    footprint_families: Iterable[str],
    version: str | None = None,
    package_sha: str | None = None,
    onboarded: Mapping[str, Any] | None = None,
) -> bool:
    """True when the script is a disarm obligation for a footprint family.

    Version-aware for power-cycle (#3723 §3.0.2): finish@1.1.0 is collect-only
    and must not count as disarm; finish@1.0.x and unknown still do.
    """
    if not script_name:
        return False
    role = step_teardown_role(
        script_name, version, package_sha=package_sha, onboarded=onboarded,
    )
    if not is_disarm_role(role):
        return False
    family = family_for_teardown_script(script_name, onboarded=onboarded)
    if family is None:
        return False
    return family in set(footprint_families)


def select_disarm_steps(
    teardown_def: Iterable[Mapping[str, Any]],
    footprint_families: Iterable[str],
    *,
    onboarded: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Filter teardown to actual disarm-role steps for footprint families.

    manual_exit / abort must run *all* actual disarm steps in pipeline order
    (#3723 §3.0.2): legacy finish@1.0.x and powercycle_disarm both qualify;
    finish@1.1.0 collect-only does not.
    """
    families = set(footprint_families)
    if not families:
        return []
    selected: list[dict[str, Any]] = []
    for step in teardown_def or []:
        if not isinstance(step, Mapping):
            continue
        name = script_name_from_step(step)
        ver = step_version_of(step)
        role = step_teardown_role(name, ver, onboarded=onboarded)
        if not is_disarm_role(role):
            continue
        family = family_for_teardown_script(name, onboarded=onboarded)
        if family is None or family not in families:
            continue
        selected.append(dict(step))
    return selected


def family_for_teardown_script(
    script_name: str,
    *,
    onboarded: Mapping[str, Any] | None = None,
) -> Optional[str]:
    """Map a teardown script name to its family (ignores ONBOARDED split).

    finish and disarm both map to power-cycle so legacy finish@1.0.x can still
    attribute disarm_results after ONBOARDED registration.
    """
    del onboarded
    if not script_name:
        return None
    for family, spec in FAMILY_STEPS.items():
        if script_name in spec["disarm"] or script_name in spec["disarm_onboarded"]:
            return family
        if script_name in spec.get("footprint", ()):
            continue
    return None


def family_for_disarm_script(
    script_name: str,
    *,
    version: str | None = None,
    package_sha: str | None = None,
    onboarded: Mapping[str, Any] | None = None,
) -> Optional[str]:
    """Family for a step that carries a disarm obligation at this version."""
    role = step_teardown_role(
        script_name, version, package_sha=package_sha, onboarded=onboarded,
    )
    if not is_disarm_role(role):
        return None
    return family_for_teardown_script(script_name, onboarded=onboarded)


def _step_enabled(step: Mapping[str, Any]) -> bool:
    enabled = step.get("enabled")
    if enabled is False:
        return False
    return True


def _step_stage(step: Mapping[str, Any]) -> str:
    stage = step.get("stage") or step.get("phase") or ""
    return str(stage).strip().lower()


def validate_onboarded_teardown_shape(
    steps: Iterable[Mapping[str, Any]],
    onboarded: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return §3.6.5 violation dicts for Plan save (empty = ok).

    Pure function: PlanStep-like mappings + ONBOARDED → gap list. Reuses
    ``step_teardown_role`` / version helpers; does not invent name/version rules.
    """
    registry = ONBOARDED if onboarded is None else onboarded
    step_list = [s for s in steps if isinstance(s, Mapping)]
    violations: list[dict[str, Any]] = []

    for family, spec in registry.items():
        if not isinstance(spec, dict):
            continue
        fam_steps = FAMILY_STEPS.get(family)
        if not fam_steps:
            continue
        footprint_names = set(fam_steps["footprint"])
        disarm_script = str(spec.get("disarm_script") or "")
        disarm_min = str(spec.get("disarm_min_version") or "")
        finish_script = str(spec.get("finish_script") or "")
        finish_min = str(spec.get("finish_min_version") or "")
        if not disarm_script or not disarm_min:
            continue

        footprint_keys: list[str] = []
        for s in step_list:
            name = s.get("script_name") or script_name_from_step(s)
            if name not in footprint_names:
                continue
            if not _step_enabled(s):
                continue
            stage = _step_stage(s)
            # footprint steps live in init; accept missing stage for engine-shaped
            if stage and stage not in ("init",):
                continue
            footprint_keys.append(str(s.get("step_key") or s.get("step_id") or name))
        if not footprint_keys:
            # no enabled footprint → only-collect plans unconstrained
            continue

        related: list[dict[str, Any]] = []
        disarm_steps: list[Mapping[str, Any]] = []
        finish_steps: list[Mapping[str, Any]] = []
        for s in step_list:
            name = str(s.get("script_name") or script_name_from_step(s) or "")
            if name not in (disarm_script, finish_script):
                continue
            stage = _step_stage(s)
            if stage and stage not in ("teardown",):
                continue
            ver = step_version_of(s)
            role = step_teardown_role(name, ver, onboarded=registry)
            entry = {
                "step_key": str(s.get("step_key") or s.get("step_id") or ""),
                "script_name": name,
                "script_version": ver,
                "enabled": _step_enabled(s),
                "role": role or "",
            }
            related.append(entry)
            if name == disarm_script:
                disarm_steps.append(s)
            if name == finish_script:
                finish_steps.append(s)

        enabled_disarm = [s for s in disarm_steps if _step_enabled(s)]
        common = {
            "family": family,
            "footprint_step_keys": list(footprint_keys),
            "required_disarm_script": disarm_script,
            "disarm_min_version": disarm_min,
            "finish_min_version": finish_min,
            "teardown_steps": list(related),
        }
        if not disarm_steps:
            violations.append({**common, "reason": TEARDOWN_REASON_DISARM_MISSING})
        elif not enabled_disarm:
            violations.append({**common, "reason": TEARDOWN_REASON_DISARM_DISABLED})
        else:
            if not any(
                version_at_least(step_version_of(s), disarm_min) for s in enabled_disarm
            ):
                violations.append({**common, "reason": TEARDOWN_REASON_DISARM_BELOW_MIN})

        enabled_finish = [s for s in finish_steps if _step_enabled(s)]
        for s in enabled_finish:
            ver = step_version_of(s)
            if not version_at_least(ver, finish_min):
                violations.append({**common, "reason": TEARDOWN_REASON_FINISH_BELOW_MIN})
                break

    return violations



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
