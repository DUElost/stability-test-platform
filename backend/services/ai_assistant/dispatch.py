# -*- coding: utf-8 -*-
"""PlanRun 派发（ADR-0031 附录 PR-B）——与 ``POST /plans/{id}/run`` 同源。"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from backend.models.host import Device
from backend.models.plan import Plan
from backend.services.ai_assistant.tools import ToolValidationError, _parse_device_ids
from backend.services.plan_dispatcher_sync import (
    PlanDispatchError,
    initial_dispatch_state,
    prepare_plan_run,
)


def normalize_dispatch_params(args: dict | None) -> dict[str, Any]:
    raw = args or {}
    plan_id = int(raw.get("plan_id") or 0)
    if plan_id < 1:
        raise ToolValidationError("plan_id must be a positive integer")
    device_ids = _parse_device_ids(raw.get("device_ids"))
    if len(device_ids) != len(set(device_ids)):
        raise ToolValidationError("device_ids must be unique")
    if any(d <= 0 for d in device_ids):
        raise ToolValidationError("device_ids must be positive integers")
    note = raw.get("note")
    if note is not None:
        note = str(note).strip()[:500] or None
    wifi_pool_id = raw.get("wifi_pool_id")
    if wifi_pool_id in (None, ""):
        wifi_pool_id = None
    else:
        try:
            wifi_pool_id = int(wifi_pool_id)
        except (TypeError, ValueError):
            raise ToolValidationError("wifi_pool_id must be an integer") from None
        if wifi_pool_id < 1:
            raise ToolValidationError("wifi_pool_id must be positive")
    return {
        "plan_id": plan_id,
        "device_ids": device_ids,
        "note": note,
        "wifi_pool_id": wifi_pool_id,
    }


def describe_dispatch_preview(db: Session, params: dict) -> str:
    plan = db.get(Plan, params["plan_id"])
    plan_name = plan.name if plan else f"#{params['plan_id']}"
    specialty = plan.specialty.key if plan and getattr(plan, "specialty", None) else None
    device_ids = params["device_ids"]
    devices = (
        db.query(Device).filter(Device.id.in_(device_ids)).all()
        if device_ids
        else []
    )
    by_id = {d.id: d for d in devices}
    lines = [
        f"Plan：{plan_name!r} (id={params['plan_id']})",
        f"专项：{specialty or '—'}",
        f"设备数：{len(device_ids)}",
    ]
    for did in device_ids:
        dev = by_id.get(did)
        if dev is None:
            lines.append(f"  - device_id={did}（未找到）")
        else:
            lines.append(
                f"  - {dev.serial} id={did} host={dev.host_id} status={dev.status}"
            )
    if params.get("note"):
        lines.append(f"备注：{params['note']}")
    if params.get("wifi_pool_id"):
        lines.append(f"WiFi 资源池 id={params['wifi_pool_id']}")
    return "\n".join(lines)


def execute_dispatch_plan_run(
    db: Session,
    params: dict,
    *,
    triggered_by: str,
) -> tuple[int, str]:
    from backend.api.routes.plans import (
        _require_active_wifi_pool,
        _require_wifi_pool_matches_plan,
    )

    plan_id = params["plan_id"]
    device_ids = params["device_ids"]
    run_context: dict = {"dispatch_state": initial_dispatch_state()}
    if params.get("note"):
        run_context["note"] = params["note"]
    wifi_pool_id = params.get("wifi_pool_id")
    if wifi_pool_id is not None:
        try:
            _require_active_wifi_pool(db, wifi_pool_id)
            _require_wifi_pool_matches_plan(db, plan_id, wifi_pool_id)
        except HTTPException as exc:
            detail = exc.detail
            if isinstance(detail, dict):
                detail = detail.get("message") or str(detail)
            raise RuntimeError(str(detail)) from exc
        run_context["wifi_pool_id"] = wifi_pool_id

    try:
        pr = prepare_plan_run(
            plan_id=plan_id,
            device_ids=device_ids,
            triggered_by=triggered_by,
            db=db,
            run_type="MANUAL",
            run_context=run_context,
        )
    except PlanDispatchError as exc:
        raise RuntimeError(str(exc)) from exc

    summary = (
        f"PlanRun #{pr.id} 已入队（status={pr.status}），"
        f"plan_id={pr.plan_id}，设备数={len(device_ids)}"
    )
    return pr.id, summary


def run_dispatch_plan_run(
    db: Session,
    params: dict,
    *,
    triggered_by: str,
    requester_user_id: int | None = None,
) -> str:
    """执行派发并写审计（与 API manual dispatch 同源）。"""
    from backend.core.audit import record_audit

    plan_run_id, summary = execute_dispatch_plan_run(db, params, triggered_by=triggered_by)
    record_audit(
        db,
        action="ai_assistant_dispatch_plan_run",
        resource_type="plan_run",
        resource_id=plan_run_id,
        details={
            "plan_id": params["plan_id"],
            "device_ids": params["device_ids"],
            "note": params.get("note"),
            "wifi_pool_id": params.get("wifi_pool_id"),
        },
        user_id=requester_user_id,
        username=triggered_by,
    )
    db.commit()
    return summary
