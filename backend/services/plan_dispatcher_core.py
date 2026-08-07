"""Shared pure helpers for sync/async plan dispatchers.

Keeps lifecycle/snapshot/error formatting logic in one place so the
sync/async wrappers only retain I/O differences.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.core.legacy_aee import LEGACY_AEE_SCRIPT_NAMES
from backend.models.host import Device, Host
from backend.models.plan import Plan, PlanStep


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

    def detail(self) -> dict | str:
        if self.disabled_legacy_scripts:
            return {
                "code": "LEGACY_AEE_SCRIPTS_DISABLED",
                "scripts": self.disabled_legacy_scripts,
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


def build_lifecycle_from_steps(
    plan: Plan, steps: list[PlanStep], script_defaults: dict[tuple[str, str], dict]
) -> dict:
    lifecycle: dict[str, Any] = {"init": [], "teardown": []}
    patrol_steps: list[dict] = []

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
        step_def: dict[str, Any] = {
            "step_id": step.step_key,
            "action": f"script:{step.script_name}",
            "version": step.script_version,
            "params": deepcopy(default_params),
            "timeout_seconds": step.timeout_seconds,
            "retry": step.retry,
        }
        if step.stall_seconds is not None:
            step_def["stall_seconds"] = step.stall_seconds

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
        step_def: dict[str, Any] = {
            "step_id": step.get("step_key"),
            "action": f"script:{script_name}",
            "version": script_version,
            "params": deepcopy(step.get("default_params") or {}),
            "timeout_seconds": step.get("timeout_seconds"),
            "retry": step.get("retry", 0),
        }
        stall = step.get("stall_seconds")
        if stall is not None:
            step_def["stall_seconds"] = stall
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


def inject_wifi_params(pipeline: dict, wifi_params: dict | None) -> dict:
    """Push the allocated WiFi credentials into the steps that consume them.

    This is the one place where step params are NOT purely ``default_params``
    (ADR-0020). Two shapes exist because the two scripts read WiFi differently:

    - ``connect_wifi`` takes ``ssid``/``password`` at the top level of params.
    - ``monkey_setup`` runs WiFi as a sub-step and reads ``params.wifi.ssid``.
      Its v2.0.0+ skips that sub-step when no ssid is present, which is what
      makes "connect WiFi" an opt-in choice per execution rather than a
      hard requirement.

    Existing values win — a plan that hardcodes an ssid is left alone.
    """
    if not wifi_params or not wifi_params.get("ssid"):
        return pipeline
    ssid = wifi_params["ssid"]
    password = wifi_params.get("password", "")
    for _, step in iter_lifecycle_steps(pipeline):
        action = step.get("action", "")
        if "connect_wifi" in action:
            params = dict(step.get("params") or {})
            if not params.get("ssid"):
                params["ssid"] = ssid
            if not params.get("password"):
                params["password"] = password
            step["params"] = params
        elif "monkey_setup" in action:
            params = dict(step.get("params") or {})
            wifi_cfg = dict(params.get("wifi") or {})
            if not wifi_cfg.get("ssid"):
                wifi_cfg["ssid"] = ssid
            if not wifi_cfg.get("password"):
                wifi_cfg["password"] = password
            params["wifi"] = wifi_cfg
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
    return {
        key: value.get("default_params") or {}
        for key, value in script_metadata.items()
    }


def build_plan_snapshot(
    plan: Plan,
    steps: list[PlanStep],
    script_metadata: dict[tuple[str, str], dict[str, dict]],
    failure_threshold: float,
) -> dict:
    return {
        "plan": {
            "id": plan.id,
            "name": plan.name,
            "description": plan.description,
            "failure_threshold": failure_threshold,
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
                "timeout_seconds": step.timeout_seconds,
                "stall_seconds": step.stall_seconds,
                "retry": step.retry,
                "enabled": step.enabled is not False,
                "sort_order": step.sort_order,
            }
            for step in sorted(steps, key=lambda s: (s.stage, s.sort_order))
        ],
    }
