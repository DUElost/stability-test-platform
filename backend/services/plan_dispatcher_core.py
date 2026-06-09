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
        default_params = script_defaults[(step.script_name, step.script_version)]
        step_def: dict[str, Any] = {
            "step_id": step.step_key,
            "action": f"script:{step.script_name}",
            "version": step.script_version,
            "params": deepcopy(default_params),
            "timeout_seconds": step.timeout_seconds,
            "retry": step.retry,
        }

        if step.stage in ("init", "teardown"):
            lifecycle[step.stage].append(step_def)
        elif step.stage == "patrol":
            patrol_steps.append(step_def)

    if patrol_steps:
        lifecycle["patrol"] = {
            "interval_seconds": plan.patrol_interval_seconds or 60,
            "steps": patrol_steps,
        }

    if plan.timeout_seconds is not None:
        lifecycle["timeout_seconds"] = plan.timeout_seconds

    return lifecycle


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
    if not wifi_params or not wifi_params.get("ssid"):
        return pipeline
    for _, step in iter_lifecycle_steps(pipeline):
        action = step.get("action", "")
        if "connect_wifi" not in action:
            continue
        params = dict(step.get("params") or {})
        if not params.get("ssid"):
            params["ssid"] = wifi_params["ssid"]
        if not params.get("password"):
            params["password"] = wifi_params.get("password", "")
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
                "retry": step.retry,
                "enabled": step.enabled is not False,
                "sort_order": step.sort_order,
            }
            for step in sorted(steps, key=lambda s: (s.stage, s.sort_order))
        ],
    }
