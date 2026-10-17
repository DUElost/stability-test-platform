"""Derive disarm_results from StepTraces (#3723 §3.0.6 / ADR-0061 D4).

Leaf helper shared by ``agent_completion`` (live /complete) and
``device_return`` (historical init). Lives outside either consumer so C5
services acyclicity holds without function-body imports.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from backend.agent.contracts.device_return import (
    DISARM_RESULT_EXECUTED_UNVERIFIED,
    DISARM_RESULT_FAILED,
    DISARM_RESULT_INCOMPLETE,
    DISARM_RESULT_NOT_EXECUTED,
    FAMILY_STEPS,
)

_SIGNAL_EXIT_CODES = {143, -15, 137, -9}


def _script_name_from_action(action: Any) -> str:
    if not isinstance(action, str) or not action.startswith("script:"):
        return ""
    return action.split(":", 1)[1].strip()


def _pipeline_steps_by_stage(pipeline_def: Any, stage: str) -> list[dict]:
    if not isinstance(pipeline_def, dict):
        return []
    lifecycle = pipeline_def.get("lifecycle") or {}
    if not isinstance(lifecycle, dict):
        return []
    steps = lifecycle.get(stage) or []
    if isinstance(steps, dict):
        steps = steps.get("steps") or []
    if not isinstance(steps, list):
        return []
    return [s for s in steps if isinstance(s, dict)]


def _step_id_to_script(pipeline_def: Any) -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    for stage in ("init", "teardown", "patrol"):
        for step in _pipeline_steps_by_stage(pipeline_def, stage):
            sid = step.get("step_id") or ""
            script = _script_name_from_action(step.get("action"))
            if sid and script:
                mapping[str(sid)] = script
    return mapping


def _family_for_script(script_name: str, role: str) -> Optional[str]:
    if not script_name:
        return None
    for family, spec in FAMILY_STEPS.items():
        names = spec.get(role) or ()
        if script_name in names:
            return family
        if role == "disarm" and script_name in (spec.get("disarm_onboarded") or ()):
            return family
    return None


def _map_trace_to_disarm_result(status: Any, exit_code: Any) -> str:
    """§3.0 第 6 条：COMPLETED / FAILED(+signal) / 其它 FAILED / 无 trace。"""
    st = str(status or "").strip().upper()
    if st in {"COMPLETED", "FINISHED", "SUCCESS", "OK"}:
        return DISARM_RESULT_EXECUTED_UNVERIFIED
    if st in {"FAILED", "ERROR", "ABORTED", "CANCELLED", "CANCELED"}:
        try:
            code = int(exit_code) if exit_code is not None else None
        except (TypeError, ValueError):
            code = None
        if code in _SIGNAL_EXIT_CODES:
            return DISARM_RESULT_INCOMPLETE
        return DISARM_RESULT_FAILED
    return DISARM_RESULT_NOT_EXECUTED


def _trace_field(tr: Any, name: str) -> Any:
    if isinstance(tr, dict):
        return tr.get(name)
    return getattr(tr, name, None)


def _is_disarm_terminal_status(status: Any) -> bool:
    st = str(status or "").strip().upper()
    return st in {
        "COMPLETED", "FINISHED", "SUCCESS", "OK",
        "FAILED", "ERROR", "ABORTED", "CANCELLED", "CANCELED",
    }


def _trace_occurrence_key(tr: Any, index: int) -> tuple:
    """Occurrence order: original_ts first; stable tie-breakers only."""
    original_ts = _trace_field(tr, "original_ts")
    if isinstance(original_ts, datetime):
        ts = original_ts if original_ts.tzinfo is not None else original_ts.replace(tzinfo=timezone.utc)
        ts_key: tuple = (0, ts.timestamp())
    elif original_ts is None:
        ts_key = (1, 0.0)
    else:
        ts_key = (1, 0.0)
    created_at = _trace_field(tr, "created_at")
    if isinstance(created_at, datetime):
        ca = created_at if created_at.tzinfo is not None else created_at.replace(tzinfo=timezone.utc)
        ca_key: tuple = (0, ca.timestamp())
    else:
        ca_key = (1, 0.0)
    trace_id = _trace_field(tr, "id")
    try:
        id_key = (0, int(trace_id))
    except (TypeError, ValueError):
        id_key = (1, index)
    return (ts_key, ca_key, id_key, index)


def derive_disarm_results_from_traces(
    pipeline_def: Any,
    traces: list[Any],
) -> Dict[str, Dict[str, Any]]:
    """从 teardown 步骤 trace 按 FAMILY_STEPS 推导 disarm_results（#3723 §3.0 第 6 条）。

    足迹：init 中 FAMILY_STEPS.footprint 步骤一旦出现任意 trace 即视为已建立。
    解除：按 original_ts 发生顺序选取该专项 teardown 解除脚本的终态；迟到的
    STARTED/RUNNING 不得覆盖其后发生的 COMPLETED/FAILED（§3.3.2 B）。
    无终态证据 → not_executed。
    """
    id_to_script = _step_id_to_script(pipeline_def)
    footprint: set[str] = set()
    disarm_events: Dict[str, list[tuple[tuple, dict]]] = {}

    for index, tr in enumerate(traces or []):
        stage = _trace_field(tr, "stage")
        step_id = _trace_field(tr, "step_id")
        status = _trace_field(tr, "status")
        exit_code = _trace_field(tr, "exit_code")
        script = id_to_script.get(str(step_id or ""), "")
        if not script:
            continue
        if stage == "init":
            fam = _family_for_script(script, "footprint")
            if fam:
                footprint.add(fam)
        elif stage == "teardown":
            fam = _family_for_script(script, "disarm")
            if fam:
                event = {
                    "status": status,
                    "exit_code": exit_code,
                    "step_id": step_id,
                }
                disarm_events.setdefault(fam, []).append(
                    (_trace_occurrence_key(tr, index), event)
                )

    families = set(footprint) | set(disarm_events)
    out: Dict[str, Dict[str, Any]] = {}
    for fam in sorted(families):
        events = sorted(disarm_events.get(fam) or [], key=lambda item: item[0])
        terminal: dict | None = None
        for _key, event in events:
            if _is_disarm_terminal_status(event.get("status")):
                terminal = event  # later original_ts wins
        if terminal is None:
            entry = {
                "result": DISARM_RESULT_NOT_EXECUTED,
                "step_id": "",
                "exit_code": None,
                "readback_summary": "",
            }
        else:
            entry = {
                "result": _map_trace_to_disarm_result(
                    terminal.get("status"), terminal.get("exit_code"),
                ),
                "step_id": str(terminal.get("step_id") or ""),
                "exit_code": terminal.get("exit_code"),
                "readback_summary": "",
            }
        out[fam] = entry
    return out
