# -*- coding: utf-8 -*-
"""T2b 自动派发白名单（ADR-0031 附录 PR-D）。"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from backend.models.plan import Plan
from backend.services.ai_assistant.tools import TOOLS

# 当前仅 dispatch_plan_run 支持 T2b 自动执行（附录 §1）。
T2B_AUTO_DISPATCH_TOOLS = frozenset({"dispatch_plan_run"})

_DEFAULT_MAX_DEVICES = 20
_MAX_DEVICES_CAP = 50


def _coerce_positive_int(value: Any, *, field: str) -> int | None:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    if n < 1:
        return None
    return n


def normalize_allowlist_entry(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    plan_id = _coerce_positive_int(raw.get("plan_id"), field="plan_id")
    if plan_id is None:
        return None
    max_devices = _coerce_positive_int(raw.get("max_devices"), field="max_devices")
    if max_devices is None:
        max_devices = _DEFAULT_MAX_DEVICES
    max_devices = min(max_devices, _MAX_DEVICES_CAP)
    tools_raw = raw.get("tools")
    if tools_raw is None:
        tools = ["dispatch_plan_run"]
    elif not isinstance(tools_raw, list):
        return None
    else:
        tools = [str(t) for t in tools_raw if str(t) in T2B_AUTO_DISPATCH_TOOLS]
    if not tools:
        return None
    return {"plan_id": plan_id, "max_devices": max_devices, "tools": tools}


def sanitize_t2b_auto_dispatch_allowlist(
    entries: list | None,
    db: Session,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """持久化前清洗：去重 plan_id、丢弃无效 Plan、仅保留合法工具名。"""
    dropped: list[dict[str, Any]] = []
    by_plan: dict[int, dict[str, Any]] = {}
    for raw in entries or []:
        norm = normalize_allowlist_entry(raw)
        if norm is None:
            dropped.append({"reason": "invalid_shape", "entry": raw})
            continue
        plan = db.get(Plan, norm["plan_id"])
        if plan is None:
            dropped.append({"reason": "plan_not_found", "entry": norm})
            continue
        by_plan[norm["plan_id"]] = norm
    return list(by_plan.values()), dropped


def dispatch_matches_t2b_allowlist(
    cfg,
    params: dict[str, Any],
    db: Session,
) -> bool:
    """参数是否命中已配置的 T2b 自动派发白名单（不含用户权限校验）。"""
    plan_id = params.get("plan_id")
    device_ids = params.get("device_ids") or []
    if not plan_id or not device_ids:
        return False
    try:
        plan_id = int(plan_id)
    except (TypeError, ValueError):
        return False
    if len(device_ids) != len(set(device_ids)):
        return False

    plan = db.get(Plan, plan_id)
    if plan is None:
        return False

    for raw in cfg.t2b_auto_dispatch_allowlist or []:
        entry = normalize_allowlist_entry(raw)
        if entry is None:
            continue
        if entry["plan_id"] != plan_id:
            continue
        if "dispatch_plan_run" not in entry["tools"]:
            continue
        if len(device_ids) > entry["max_devices"]:
            continue
        spec = TOOLS.get("dispatch_plan_run")
        if spec is None:
            return False
        return True
    return False
