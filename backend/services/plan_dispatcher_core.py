"""Shared pure helpers for sync/async plan dispatchers.

Keeps lifecycle/snapshot/error formatting logic in one place so the
sync/async wrappers only retain I/O differences.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Mapping, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.agent.contracts.legacy_aee import LEGACY_AEE_SCRIPT_NAMES
from backend.models.host import Device, Host
from backend.models.plan import Plan, PlanStep
from backend.services.project_log_profile import (
    TARGET_SCRIPT_NAME as _LOG_PROFILE_SCRIPT_NAME,
    extract_applied_log_profile,
)
from backend.services.resource_pool import (
    DEFAULT_EGRESS,
    VALID_EGRESSES,
    VALID_RESOURCE_TYPES,
    pool_egress,
)
from backend.services.script_params import merge_effective_params


class PlanDispatchError(Exception):
    """Unified dispatcher error with optional structured metadata."""

    def __init__(
        self,
        message: str,
        *,
        missing_scripts: list[str] | None = None,
        unavailable_devices: list[dict] | None = None,
        mixed_watcher_inactive_host_ids: list[str] | None = None,
        disabled_legacy_scripts: list[str] | None = None,
        suite_binding_required: list[str] | None = None,
    ) -> None:
        super().__init__(message)
        self.missing_scripts = list(missing_scripts) if missing_scripts else None
        self.unavailable_devices = (
            list(unavailable_devices) if unavailable_devices else None
        )
        self.mixed_watcher_inactive_host_ids = (
            list(mixed_watcher_inactive_host_ids)
            if mixed_watcher_inactive_host_ids
            else None
        )
        self.disabled_legacy_scripts = (
            list(disabled_legacy_scripts) if disabled_legacy_scripts else None
        )
        self.suite_binding_required = (
            list(suite_binding_required) if suite_binding_required else None
        )

    def detail(self) -> dict | str:
        if self.disabled_legacy_scripts:
            return {
                "code": "LEGACY_AEE_SCRIPTS_DISABLED",
                "scripts": self.disabled_legacy_scripts,
            }
        if self.suite_binding_required:
            return {
                "code": "SUITE_BINDING_REQUIRED",
                "mtbf_steps": self.suite_binding_required,
            }
        if self.missing_scripts:
            return {
                "code": "INVALID_SCRIPT_REFS",
                "missing": self.missing_scripts,
            }
        if self.unavailable_devices:
            return {
                "code": "DEVICES_UNAVAILABLE",
                "unavailable_devices": self.unavailable_devices,
            }
        if self.mixed_watcher_inactive_host_ids:
            return {
                "code": "MIXED_WATCHER_ACTIVITY",
                "message": str(self),
                "inactive_host_ids": self.mixed_watcher_inactive_host_ids,
            }
        return str(self)


def snapshot_dispatch_host_watcher_admin_states(
    db: Session, device_ids: list[int],
) -> Dict[str, bool]:
    """Freeze host watcher admin state for this dispatch attempt.

    Why: admin 手工切换只应影响"后续新派发任务"。一旦 PlanRun 已 prepare，
    后续 gate / claim / recovery 都必须消费同一份快照，而不是回读 Host 当前状态。
    """
    if not device_ids:
        return {}

    rows = db.execute(
        select(Device.host_id, Host.watcher_admin_active)
        .select_from(Device)
        .outerjoin(Host, Device.host_id == Host.id)
        .where(Device.id.in_(device_ids))
    ).all()

    state_map: Dict[str, bool] = {}
    for row in rows:
        host_id = row.host_id
        if not host_id:
            continue
        state_map[str(host_id)] = (
            True if row.watcher_admin_active is None else bool(row.watcher_admin_active)
        )
    return {hid: state_map[hid] for hid in sorted(state_map)}


def extract_dispatch_host_watcher_admin_states(
    run_context: Any,
) -> Dict[str, bool]:
    """从 PlanRun.run_context JSONB 中提取 dispatch_host_watcher_admin_states 快照。

    供 claim / recovery 路径消费，保证 host watcher 状态以派发时快照为准，
    而非回读 Host 当前值。
    """
    if not isinstance(run_context, dict):
        return {}
    snapshot = run_context.get("dispatch_host_watcher_admin_states")
    if not isinstance(snapshot, dict):
        return {}
    return {
        str(host_id): True if is_active is None else bool(is_active)
        for host_id, is_active in snapshot.items()
    }


def apply_dispatch_host_watcher_admin_state_to_policy(
    watcher_policy: Optional[Dict[str, Any]],
    *,
    host_id: Optional[str],
    dispatch_host_watcher_admin_states: Optional[Dict[str, bool]],
) -> Optional[Dict[str, Any]]:
    """将 dispatch 快照中 host 的 watcher 管控状态合并到 watcher_policy。

    若快照标记该 host inactive，则强制 enabled=False 并保留其余 policy 字段；
    否则原样返回。
    """
    if not host_id or not dispatch_host_watcher_admin_states:
        return watcher_policy
    if dispatch_host_watcher_admin_states.get(str(host_id), True):
        return watcher_policy

    effective_policy = dict(watcher_policy or {})
    effective_policy["enabled"] = False
    return effective_policy


def check_script_keys_complete(
    steps: list[PlanStep],
    metadata: dict[tuple[str, str], dict[str, dict]],
) -> list[str]:
    required = {
        (step.script_name, step.script_version)
        for step in steps
        if step.enabled is not False
    }
    have = set(metadata.keys())
    missing = sorted(required - have)
    return [f"{name}:{version}" for name, version in missing]


def check_legacy_aee_script_refs(steps: list[PlanStep]) -> list[str]:
    disabled = sorted(
        {
            f"{step.script_name}:{step.script_version}"
            for step in steps
            if step.enabled is not False
            and step.script_name in LEGACY_AEE_SCRIPT_NAMES
        }
    )
    return disabled


def check_suite_binding_required(
    plan: Plan, steps: list[PlanStep]
) -> list[str]:
    """ADR-0030 v1.8：mtbf 系脚本必须绑定套件，返回未绑定的 mtbf step_key。

    观测期（WARNING ``suite_unbound``）结论为零存量未绑定流量、唯一 mtbf
    Plan 已绑定，按 issue #404 口径翻转硬拒。非 mtbf 计划不受影响；
    P0 文件真源模式对 mtbf 脚本就此关闭（套件是唯一配置通道）。
    """
    if plan.suite_id is not None:
        return []
    return sorted(
        step.step_key
        for step in steps
        if step.enabled is not False
        and step.script_name.startswith("mtbf_")
    )


def apply_step_timing_fields(
    step_def: dict[str, Any],
    *,
    timeout_seconds: int | None,
    stall_seconds: int | None,
) -> dict[str, Any]:
    """步骤的两个时间字段共用一条规则：**未配置就不写这个键**（#2382）。

    「写 `None`」与「不写键」在 schema 上不等价：`timeout_seconds: null` 会被
    `{"type": "integer"}` 拒掉，而省略键才表达「未配置」——Agent 据此回落
    `STP_STEP_WALL_CLOCK_SECONDS`，未设再回落 300s
    （`backend/agent/pipeline_engine.py::_resolve_step_wall_clock`）。
    `stall_seconds` 从一开始就走这条规则，`timeout_seconds` 漏了，于是严格照
    OpenAPI 省略该字段的调用方一律 422。两个字段收进同一个函数，是为了不再出现
    「一处修法被另一个字段遗忘」。
    """
    if timeout_seconds is not None:
        step_def["timeout_seconds"] = timeout_seconds
    if stall_seconds is not None:
        step_def["stall_seconds"] = stall_seconds
    return step_def


def build_lifecycle_from_steps(
    plan: Plan,
    steps: list[PlanStep],
    script_defaults: dict[tuple[str, str], dict],
    *,
    project_log_profile: Optional[Mapping[str, Any]] = None,
) -> dict:
    lifecycle: dict[str, Any] = {"init": [], "teardown": []}
    patrol_steps: list[dict] = []
    applied_preset = extract_applied_log_profile(project_log_profile)

    for step in sorted(steps, key=lambda s: (s.stage, s.sort_order)):
        if step.enabled is False:
            continue
        key = (step.script_name, step.script_version)
        if key not in script_defaults:
            raise PlanDispatchError(
                f"Script {step.script_name}@{step.script_version} referenced by "
                f"step '{step.step_key}' is not found or has been deactivated. "
                f"Update the step to an active version before dispatching."
            )
        default_params = script_defaults[key]
        # ADR-0067 D2 唯一五层静态合并入口（schema.default + default_params 已在
        # script_defaults 内合完，此处依次叠加第 3 层 project_preset 与第 4 层 step.params）。
        step_preset = (
            applied_preset if step.script_name == _LOG_PROFILE_SCRIPT_NAME else None
        )
        merged_params = merge_effective_params(
            None,
            default_params,
            getattr(step, "params", None),
            project_preset=step_preset,
        )
        step_def: dict[str, Any] = {
            "step_id": step.step_key,
            "action": f"script:{step.script_name}",
            "version": step.script_version,
            "params": merged_params,
            "retry": step.retry,
        }
        apply_step_timing_fields(
            step_def,
            timeout_seconds=step.timeout_seconds,
            stall_seconds=step.stall_seconds,
        )

        if step.stage in ("init", "teardown"):
            lifecycle[step.stage].append(step_def)
        elif step.stage == "patrol":
            patrol_steps.append(step_def)

    if patrol_steps:
        if plan.patrol_interval_seconds is None:
            raise PlanDispatchError(
                "enabled patrol steps require patrol_interval_seconds"
            )
        lifecycle["patrol"] = {
            "interval_seconds": plan.patrol_interval_seconds,
            "steps": patrol_steps,
        }
    elif plan.patrol_interval_seconds is not None:
        raise PlanDispatchError(
            "patrol_interval_seconds requires enabled patrol steps"
        )

    if plan.timeout_seconds is not None:
        lifecycle["timeout_seconds"] = plan.timeout_seconds

    # #117: barrier 预算随计划走。NULL 时不写入，Agent 侧回落到 env / 600s。
    barrier_timeout = getattr(plan, "barrier_timeout_seconds", None)
    if barrier_timeout is not None:
        lifecycle["barrier_timeout_seconds"] = barrier_timeout
    # #174: barrier 绝对硬顶随计划走。NULL 时不写入（Agent 保持无硬顶行为）。
    barrier_max_wait = getattr(plan, "barrier_max_wait_seconds", None)
    if barrier_max_wait is not None:
        lifecycle["barrier_max_wait_seconds"] = barrier_max_wait

    return lifecycle


def build_lifecycle_from_snapshot(plan_snapshot: dict) -> dict:
    """Materialize an immutable lifecycle from a PlanRun snapshot."""
    snapshot = plan_snapshot or {}
    plan_data = snapshot.get("plan") or {}
    snapshot_steps = snapshot.get("steps") or []
    # ADR-0067 D5.2：只消费冻结的 plan_snapshot["project_log_profile"]，绝不回读活表；
    # 历史快照缺失该键按未启用处理（D5.3）。
    applied_preset = extract_applied_log_profile(snapshot.get("project_log_profile"))
    lifecycle: dict[str, Any] = {"init": [], "teardown": []}
    patrol_steps: list[dict[str, Any]] = []

    for step in sorted(
        snapshot_steps,
        key=lambda item: (item.get("stage", ""), item.get("sort_order", 0)),
    ):
        if not isinstance(step, dict) or step.get("enabled") is False:
            continue
        stage = step.get("stage")
        if stage not in {"init", "patrol", "teardown"}:
            raise PlanDispatchError(f"invalid snapshot stage: {stage!r}")
        script_name = step.get("script_name")
        script_version = step.get("script_version")
        if not script_name or not script_version:
            raise PlanDispatchError("snapshot step is missing script identity")
        # #508/#977/ADR-0067 D2：schema.default < default_params < project_preset < step.params
        step_preset = (
            applied_preset if script_name == _LOG_PROFILE_SCRIPT_NAME else None
        )
        merged_params = merge_effective_params(
            step.get("param_schema"),
            step.get("default_params"),
            step.get("params"),
            project_preset=step_preset,
        )
        step_def: dict[str, Any] = {
            "step_id": step.get("step_key"),
            "action": f"script:{script_name}",
            "version": script_version,
            "params": merged_params,
            "retry": step.get("retry", 0),
        }
        apply_step_timing_fields(
            step_def,
            timeout_seconds=step.get("timeout_seconds"),
            stall_seconds=step.get("stall_seconds"),
        )
        if stage == "patrol":
            patrol_steps.append(step_def)
        else:
            lifecycle[stage].append(step_def)

    if patrol_steps:
        interval = plan_data.get("patrol_interval_seconds")
        if not isinstance(interval, int) or interval < 1:
            raise PlanDispatchError(
                "snapshot patrol steps require patrol_interval_seconds"
            )
        lifecycle["patrol"] = {
            "interval_seconds": interval,
            "steps": patrol_steps,
        }
    elif plan_data.get("patrol_interval_seconds") is not None:
        raise PlanDispatchError(
            "snapshot patrol_interval_seconds requires enabled patrol steps"
        )

    timeout_seconds = plan_data.get("timeout_seconds")
    if timeout_seconds is not None:
        lifecycle["timeout_seconds"] = timeout_seconds
    # #117: 快照路径同样要带 barrier 预算，否则重放旧 PlanRun 会悄悄退回 600s。
    barrier_timeout = plan_data.get("barrier_timeout_seconds")
    if barrier_timeout is not None:
        lifecycle["barrier_timeout_seconds"] = barrier_timeout
    # #174: 快照路径同样带绝对硬顶。
    barrier_max_wait = plan_data.get("barrier_max_wait_seconds")
    if barrier_max_wait is not None:
        lifecycle["barrier_max_wait_seconds"] = barrier_max_wait
    return lifecycle


_WIFI_CONSUMER_SCRIPT_NAMES = frozenset({"connect_wifi", "monkey_setup"})
_NETWORK_CONSUMER_SCRIPT_NAMES = frozenset(
    {"connect_wifi", "monkey_setup", "setup_network"}
)


@dataclass(frozen=True)
class PlanNetworkRequirements:
    """Intersection of enabled network-supply steps in a Plan or lifecycle (ADR-0066 D3.3)."""

    has_network_steps: bool
    has_setup_network: bool
    has_connect_wifi: bool
    has_monkey_setup: bool
    allowed_types: frozenset[str]
    allowed_egresses: frozenset[str]
    setup_network_required: bool
    conflict_reason: Optional[str] = None

    @property
    def is_legacy_only(self) -> bool:
        return self.has_network_steps and not self.has_setup_network

    @property
    def is_contradictory(self) -> bool:
        return self.conflict_reason is not None or (
            self.has_network_steps
            and (not self.allowed_types or not self.allowed_egresses)
        )


def _compute_network_requirements(
    enabled_steps: Iterable[tuple[str, dict[str, Any]]],
) -> PlanNetworkRequirements:
    has_setup_network = False
    has_connect_wifi = False
    has_monkey_setup = False
    setup_required = False
    allowed_types: set[str] = set(VALID_RESOURCE_TYPES)
    allowed_egresses: set[str] = set(VALID_EGRESSES)
    conflict_reason: Optional[str] = None

    for script_name, params in enabled_steps:
        if script_name == "setup_network":
            has_setup_network = True
            raw_type = (params.get("required_type") or "").strip()
            if raw_type == "":
                step_types = set(VALID_RESOURCE_TYPES)
            elif raw_type in VALID_RESOURCE_TYPES:
                step_types = {raw_type}
            else:
                step_types = set()
                conflict_reason = f"unsupported setup_network required_type {raw_type!r}"
            raw_egress = params.get("required_egress")
            egress_val = DEFAULT_EGRESS if raw_egress in (None, "") else str(raw_egress).strip()
            if egress_val in VALID_EGRESSES:
                step_egresses = {egress_val}
            else:
                step_egresses = set()
                conflict_reason = f"unsupported setup_network required_egress {egress_val!r}"
            if bool(params.get("required", False)):
                setup_required = True
            allowed_types &= step_types
            allowed_egresses &= step_egresses
        elif script_name == "connect_wifi":
            has_connect_wifi = True
            allowed_types &= {"wifi"}
            allowed_egresses &= set(VALID_EGRESSES)
        elif script_name == "monkey_setup":
            has_monkey_setup = True
            allowed_types &= {"wifi"}
            allowed_egresses &= set(VALID_EGRESSES)

    has_network = has_setup_network or has_connect_wifi or has_monkey_setup
    if has_network and conflict_reason is None:
        if not allowed_types:
            conflict_reason = (
                "conflicting network supply types across enabled steps "
                "(e.g. setup_network(required_type='usb_net') combined with connect_wifi/monkey_setup)"
            )
        elif not allowed_egresses:
            conflict_reason = (
                "conflicting network egress policies across enabled steps "
                "(direct vs vpn)"
            )
        elif allowed_types == {"usb_net"} and allowed_egresses == {"vpn"}:
            conflict_reason = (
                "network step intersection requires usb_net + vpn which is disabled in Phase 1"
            )

    return PlanNetworkRequirements(
        has_network_steps=has_network,
        has_setup_network=has_setup_network,
        has_connect_wifi=has_connect_wifi,
        has_monkey_setup=has_monkey_setup,
        allowed_types=frozenset(allowed_types) if has_network else frozenset(),
        allowed_egresses=frozenset(allowed_egresses) if has_network else frozenset(),
        setup_network_required=setup_required,
        conflict_reason=conflict_reason,
    )


def analyze_plan_steps_network_requirements(
    steps: Iterable[Any],
) -> PlanNetworkRequirements:
    """Compute intersection requirements from ``PlanStep`` or ``PlanStepIn`` objects."""
    items: list[tuple[str, dict[str, Any]]] = []
    for step in steps:
        if getattr(step, "enabled", True) is False:
            continue
        script_name = getattr(step, "script_name", "") or ""
        if script_name in _NETWORK_CONSUMER_SCRIPT_NAMES:
            params = getattr(step, "params", None)
            items.append((script_name, dict(params) if isinstance(params, dict) else {}))
    return _compute_network_requirements(items)


def _script_name_from_action(action: str | None) -> str:
    raw = (action or "").strip()
    if raw.startswith("script:"):
        return raw[len("script:"):]
    return raw


def analyze_lifecycle_network_requirements(lifecycle: dict) -> PlanNetworkRequirements:
    """Compute intersection requirements from a materialized ``lifecycle`` dict."""
    items: list[tuple[str, dict[str, Any]]] = []
    for _, step in iter_lifecycle_steps({"lifecycle": lifecycle}):
        script_name = _script_name_from_action(step.get("action"))
        if script_name in _NETWORK_CONSUMER_SCRIPT_NAMES:
            params = step.get("params")
            items.append((script_name, dict(params) if isinstance(params, dict) else {}))
    return _compute_network_requirements(items)


def plan_steps_consumes_network(steps: Iterable[Any]) -> bool:
    """True when any enabled step consumes network supply (setup_network/connect_wifi/monkey_setup)."""
    return analyze_plan_steps_network_requirements(steps).has_network_steps


def plan_steps_consumes_wifi(steps: list[PlanStep]) -> bool:
    """True when any enabled step runs a script that can consume WiFi credentials."""
    for step in steps:
        if step.enabled is False:
            continue
        if step.script_name in _WIFI_CONSUMER_SCRIPT_NAMES:
            return True
    return False


def plan_steps_has_connect_wifi(steps: list[PlanStep]) -> bool:
    """True when any enabled step is the dedicated ``connect_wifi`` script."""
    for step in steps:
        if step.enabled is False:
            continue
        if step.script_name == "connect_wifi":
            return True
    return False


def lifecycle_consumes_network(lifecycle: dict) -> bool:
    """True when any lifecycle step targets a network-supply script."""
    return analyze_lifecycle_network_requirements(lifecycle).has_network_steps


def lifecycle_has_setup_network(lifecycle: dict) -> bool:
    """True when any lifecycle step targets ``script:setup_network``."""
    return analyze_lifecycle_network_requirements(lifecycle).has_setup_network


def lifecycle_consumes_wifi(lifecycle: dict) -> bool:
    """True when any lifecycle step action targets a WiFi-consuming script."""
    for _, step in iter_lifecycle_steps({"lifecycle": lifecycle}):
        action = step.get("action") or ""
        if any(name in action for name in _WIFI_CONSUMER_SCRIPT_NAMES):
            return True
    return False


def lifecycle_has_connect_wifi_step(lifecycle: dict) -> bool:
    for _, step in iter_lifecycle_steps({"lifecycle": lifecycle}):
        if "connect_wifi" in (step.get("action") or ""):
            return True
    return False


def iter_lifecycle_steps(pipeline: dict):
    lifecycle = (pipeline or {}).get("lifecycle", {})
    for phase_name in ("init", "teardown"):
        steps = lifecycle.get(phase_name)
        if isinstance(steps, list):
            for step in steps:
                yield phase_name, step
    patrol = lifecycle.get("patrol")
    if isinstance(patrol, dict) and isinstance(patrol.get("steps"), list):
        for step in patrol["steps"]:
            yield "patrol", step


def _is_empty_str_param(value: Any) -> bool:
    """ADR-0067 D2.5：字符串凭据/项目键填空谓词（缺键/None/空串，不把 False 视为空）。"""
    return value is None or value == ""


def _is_empty_count_param(value: Any) -> bool:
    """ADR-0067 D2.5：计数字段填空谓词（缺键/None/0，不把 False 视为空）。"""
    return value is None or (value == 0 and not isinstance(value, bool))


def inject_wifi_params(pipeline: dict, wifi_params: dict | None) -> dict:
    """Push the allocated WiFi credentials into the steps that consume them.

    This is the one place where step params are NOT purely ``default_params``
    (ADR-0020). Two shapes exist because the two scripts read WiFi differently:

    - ``connect_wifi`` takes ``ssid``/``password`` at the top level of params.
    - ``monkey_setup`` runs WiFi as a sub-step and reads ``params.wifi.ssid``.
      Its v2.0.0+ skips that sub-step when no ssid is present, which is what
      makes "connect WiFi" an opt-in choice per execution rather than a
      hard requirement.

    Existing non-empty values win — a plan that hardcodes an ssid is left alone.
    """
    if not wifi_params or not wifi_params.get("ssid"):
        return pipeline
    ssid = wifi_params["ssid"]
    password = wifi_params.get("password", "")
    for _, step in iter_lifecycle_steps(pipeline):
        action = step.get("action", "")
        if "connect_wifi" in action:
            params = dict(step.get("params") or {})
            if _is_empty_str_param(params.get("ssid")):
                params["ssid"] = ssid
            if _is_empty_str_param(params.get("password")):
                params["password"] = password
            step["params"] = params
        elif "monkey_setup" in action:
            params = dict(step.get("params") or {})
            wifi_cfg = dict(params.get("wifi") or {})
            if _is_empty_str_param(wifi_cfg.get("ssid")):
                wifi_cfg["ssid"] = ssid
            if _is_empty_str_param(wifi_cfg.get("password")):
                wifi_cfg["password"] = password
            params["wifi"] = wifi_cfg
            step["params"] = params
    return pipeline


def inject_network_params(pipeline: dict, network_params: dict | None) -> dict:
    """Inject allocated network-supply pool parameters into ``setup_network`` & legacy steps.

    - For ``wifi`` pools: populates ``setup_network`` plus legacy ``connect_wifi`` /
      ``monkey_setup`` (preserving ``inject_wifi_params`` semantics).
    - For ``usb_net`` pools: populates ``setup_network`` only (never injects into
      legacy WiFi steps).
    """
    if not network_params:
        return pipeline
    resource_type = (network_params.get("resource_type") or "wifi").strip()
    egress = pool_egress(network_params)
    pool_id = network_params.get("pool_id")
    pool_name = network_params.get("pool_name", "")

    if resource_type == "wifi":
        inject_wifi_params(pipeline, network_params)

    for _, step in iter_lifecycle_steps(pipeline):
        if _script_name_from_action(step.get("action")) != "setup_network":
            continue
        params = dict(step.get("params") or {})
        params["resource_type"] = resource_type
        params["egress"] = egress
        if pool_id is not None:
            params["network_pool_id"] = pool_id
        if pool_name:
            params["pool_name"] = pool_name
        if resource_type == "wifi":
            if network_params.get("ssid") and _is_empty_str_param(params.get("ssid")):
                params["ssid"] = network_params["ssid"]
            if network_params.get("password") and _is_empty_str_param(params.get("password")):
                params["password"] = network_params["password"]
        elif resource_type == "usb_net":
            for key in ("bridge_name", "subnet_cidr", "max_devices_per_host"):
                if network_params.get(key) is not None:
                    params[key] = network_params[key]
        step["params"] = params
    return pipeline


def inject_suite_params(pipeline: dict, suite_params: dict | None) -> dict:
    """ADR-0030 §3.4 — push managed-suite params into ``mtbf_*`` steps.

    Second (and last) sanctioned deviation from "params are purely
    ``default_params``" alongside :func:`inject_wifi_params`: when a Plan is
    bound to a managed test suite, the dispatcher computes
    ``{expected_testpoint_count, project}`` from the frozen
    ``run_context.dispatch_suite`` and fills them into every step whose action
    is a ``script:mtbf_*`` script — no user-declared default_params required.
    The values flow to the device scripts through the existing
    ``STP_STEP_PARAMS`` channel; unbound plans are untouched (env fallback in
    the scripts still applies).

    Existing values win for fill-in — except ``project`` (#975): a declared
    value that disagrees with the gated suite ``export_dir`` is rejected so
    materialize cannot consume a different tool directory than precheck.
    """
    if not suite_params:
        return pipeline
    expected_project = suite_params.get("project")
    for _, step in iter_lifecycle_steps(pipeline):
        action = step.get("action") or ""
        if not action.startswith("script:mtbf_"):
            continue
        params = dict(step.get("params") or {})
        declared_project = params.get("project")
        if (
            expected_project not in (None, "")
            and declared_project not in (None, "")
            and str(declared_project) != str(expected_project)
        ):
            raise PlanDispatchError(
                "mtbf step project conflicts with suite export_dir "
                f"(declared={declared_project!r}, expected={expected_project!r})",
            )
        for key, value in suite_params.items():
            cur = params.get(key)
            if key == "expected_testpoint_count":
                if _is_empty_count_param(cur):
                    params[key] = value
            elif _is_empty_str_param(cur):
                params[key] = value
        step["params"] = params
    return pipeline


def build_preview(plan: Plan, lifecycle: dict, device_ids: list[int]) -> dict:
    steps = list(iter_lifecycle_steps({"lifecycle": lifecycle}))
    return {
        "plan_id": plan.id,
        "plan_name": plan.name,
        "device_ids": device_ids,
        "device_count": len(device_ids),
        "job_count": len(device_ids),
        "total_steps": len(steps),
        "lifecycle": lifecycle,
    }


def script_defaults(
    script_metadata: dict[tuple[str, str], dict[str, dict]]
) -> dict[tuple[str, str], dict]:
    """有效脚本默认 = ``schema.default`` 为底、``default_params`` 覆盖（#977：
    与前端展示的「step.params > default_params > schema.default」同优先级；
    步骤级 ``step.params`` 在 build_lifecycle_from_steps 再覆盖一层）。"""
    return {
        key: merge_effective_params(
            value.get("param_schema"), value.get("default_params"),
        )
        for key, value in script_metadata.items()
    }


def build_plan_snapshot(
    plan: Plan,
    steps: list[PlanStep],
    script_metadata: dict[tuple[str, str], dict[str, dict]],
    *,
    project_log_profile: Optional[Mapping[str, Any]] = None,
) -> dict:
    snapshot: dict[str, Any] = {
        "plan": {
            "id": plan.id,
            "name": plan.name,
            "description": plan.description,
            "patrol_interval_seconds": plan.patrol_interval_seconds,
            "timeout_seconds": plan.timeout_seconds,
            "barrier_timeout_seconds": plan.barrier_timeout_seconds,
            "barrier_max_wait_seconds": plan.barrier_max_wait_seconds,
            "auto_archive_interval_seconds": plan.auto_archive_interval_seconds,
            "next_plan_id": plan.next_plan_id,
            "watcher_policy": plan.watcher_policy or {},
        },
        "steps": [
            {
                "stage": step.stage,
                "step_key": step.step_key,
                "script_name": step.script_name,
                "script_version": step.script_version,
                "nfs_path": (
                    script_metadata
                    .get((step.script_name, step.script_version), {})
                    .get("nfs_path", "")
                ),
                "param_schema": (
                    script_metadata
                    .get((step.script_name, step.script_version), {})
                    .get("param_schema", {})
                ),
                "default_params": (
                    script_metadata
                    .get((step.script_name, step.script_version), {})
                    .get("default_params", {})
                ),
                # #508 步骤级 params：固化原始步骤覆写，不写回 project_log_profile（ADR-0067 D5.1）。
                "params": getattr(step, "params", None) or {},
                "timeout_seconds": step.timeout_seconds,
                "stall_seconds": step.stall_seconds,
                "retry": step.retry,
                "enabled": step.enabled is not False,
                "sort_order": step.sort_order,
                # ADR-0065 / B12 U4：派发快照冻结 atomic_group 与 skip 交付口径，
                # 供预登记第二层冻结与 Agent 展开校验（U5/U6）消费。
                "atomic_group": getattr(step, "atomic_group", None),
                "skip_satisfies_delivery": bool(
                    getattr(step, "skip_satisfies_delivery", False)
                ),
            }
            for step in sorted(steps, key=lambda s: (s.stage, s.sort_order))
        ],
    }
    if project_log_profile is not None:
        snapshot["project_log_profile"] = deepcopy(dict(project_log_profile))
    return snapshot
