"""Shared pure helpers for sync/async plan dispatchers.

Keeps lifecycle/snapshot/error formatting logic in one place so the
sync/async wrappers only retain I/O differences.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from backend.models.plan import Plan, PlanStep


class PlanDispatchError(Exception):
    """Unified dispatcher error with optional structured script metadata."""

    def __init__(
        self, message: str, *, missing_scripts: list[str] | None = None
    ) -> None:
        super().__init__(message)
        self.missing_scripts = list(missing_scripts) if missing_scripts else None

    def detail(self) -> dict | str:
        if self.missing_scripts:
            return {
                "code": "INVALID_SCRIPT_REFS",
                "missing": self.missing_scripts,
            }
        return str(self)


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
