"""L1 确认指纹（#3653 §3.2，ADR-0060 D5）。

preview 与 prepare 共用本模块的 canonical 文档与 HMAC。密钥是现有
``JWT_SECRET_KEY``（``backend.core.security.SECRET_KEY``），不新增 env，
不记录密钥、canonical 原文或摘要输入。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.core.security import SECRET_KEY
from backend.models.plan import Plan, PlanStep
from backend.services.plan_dispatcher_core import PlanDispatchError

FINGERPRINT_PREFIX = "stp-l1-v1:"
DOMAIN_SEPARATOR = "stp-plan-confirmation-v1"
_FINGERPRINT_RE = re.compile(r"^stp-l1-v1:[0-9a-f]{64}$")

_PLAN_FIELDS = (
    "id",
    "name",
    "description",
    "patrol_interval_seconds",
    "timeout_seconds",
    "barrier_timeout_seconds",
    "barrier_max_wait_seconds",
    "auto_archive_interval_seconds",
    "watcher_policy",
    "next_plan_id",
    "project_id",
    "specialty_id",
    "suite_id",
    "created_by",
    "updated_at",
)

class PlanConfirmationChanged(PlanDispatchError):
    """预览指纹与 prepare 实际读取的配置不一致。不得携带原配置或新 token。"""

    def __init__(self) -> None:
        super().__init__("计划配置已变化，请重新预览并确认")


def fingerprint_format_ok(value: str) -> bool:
    return bool(_FINGERPRINT_RE.fullmatch(value))


def _utc_text(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _json_ready(value: Any) -> Any:
    if isinstance(value, datetime):
        return _utc_text(value)
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    return value


def _schema_defaults(param_schema: Any) -> dict[str, Any]:
    """只保留决定合并值的 schema.default，丢掉 label/description。"""
    if not isinstance(param_schema, dict):
        return {}
    defaults: dict[str, Any] = {}
    for key, field in param_schema.items():
        if isinstance(field, dict) and "default" in field:
            defaults[str(key)] = deepcopy(field["default"])
    return defaults


def _step_sort_key(step: PlanStep) -> tuple:
    return (
        step.stage or "",
        0 if step.sort_order is None else step.sort_order,
        step.step_key or "",
        0 if step.id is None else step.id,
    )


def canonical_confirmation_document(
    plan: Plan,
    steps: list[PlanStep],
    script_metadata: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, Any]:
    """确认对象。说明、设备/WiFi 选择、套件内容与主机管控不进入此文档。"""
    plan_doc = {
        name: _json_ready(getattr(plan, name))
        for name in _PLAN_FIELDS
    }
    step_docs = []
    referenced: set[tuple[str, str]] = set()
    for step in sorted(steps, key=_step_sort_key):
        referenced.add((step.script_name, step.script_version))
        step_docs.append({
            "id": step.id,
            "plan_id": step.plan_id,
            "step_key": step.step_key,
            "script_name": step.script_name,
            "script_version": step.script_version,
            "stage": step.stage,
            "sort_order": step.sort_order,
            "timeout_seconds": step.timeout_seconds,
            "stall_seconds": step.stall_seconds,
            "params": _json_ready(deepcopy(step.params) if step.params is not None else None),
            "retry": step.retry,
            "enabled": bool(step.enabled),
            "created_at": _utc_text(step.created_at),
        })
    scripts = []
    for name, version in sorted(referenced):
        meta = script_metadata.get((name, version), {})
        scripts.append({
            "name": name,
            "version": version,
            "schema_defaults": _schema_defaults(meta.get("param_schema")),
            "default_params": _json_ready(deepcopy(meta.get("default_params") or {})),
        })
    return {"plan": plan_doc, "steps": step_docs, "scripts": scripts}


def _canonical_bytes(document: dict[str, Any]) -> bytes:
    return json.dumps(
        document,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def confirmation_fingerprint(
    plan: Plan,
    steps: list[PlanStep],
    script_metadata: dict[tuple[str, str], dict[str, Any]],
) -> str:
    payload = _canonical_bytes(
        canonical_confirmation_document(plan, steps, script_metadata),
    )
    digest = hmac.new(
        SECRET_KEY.encode("utf-8"),
        DOMAIN_SEPARATOR.encode("utf-8") + b"\0" + payload,
        hashlib.sha256,
    ).hexdigest()
    return FINGERPRINT_PREFIX + digest


def assert_confirmation_matches(
    plan: Plan,
    steps: list[PlanStep],
    script_metadata: dict[tuple[str, str], dict[str, Any]],
    presented: str | None,
) -> None:
    """缺省或 NULL 不比较。已携带的指纹必须与本次实际读取对象一致。"""
    if presented is None:
        return
    expected = confirmation_fingerprint(plan, steps, script_metadata)
    if not hmac.compare_digest(expected, presented):
        raise PlanConfirmationChanged()


def _sqlite(db: Session) -> bool:
    bind = db.get_bind()
    return bind is not None and bind.dialect.name.startswith("sqlite")


def load_plan_graph_for_confirmation(
    db: Session, plan_id: int,
) -> tuple[Plan | None, list[PlanStep]]:
    """读取阶段对 Plan 取 PostgreSQL FOR SHARE，并刷新身份映射。

    与 ``update_plan`` 的 FOR UPDATE 配对：校验通过到 snapshot 落库仍在同一
    事务，写者不能插入更改。SQLite 没有行锁，调用方不得把该方言当成锁证据。
    """
    plan_stmt = select(Plan).where(Plan.id == plan_id)
    if not _sqlite(db):
        plan_stmt = plan_stmt.with_for_update(read=True)
    plan = db.execute(
        plan_stmt,
        execution_options={"populate_existing": True},
    ).scalar_one_or_none()
    if plan is None:
        return None, []
    steps = list(
        db.execute(
            select(PlanStep)
            .where(PlanStep.plan_id == plan_id)
            .execution_options(populate_existing=True),
        ).scalars().all()
    )
    return plan, steps
