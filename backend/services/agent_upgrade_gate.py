"""Agent 升级门禁 HTTP 适配（#1520 垂直切片：agent_api upgrade-gate）。

``POST /hosts/{id}/upgrade-gate`` / ``.../release``：把
``host_upgrade_gate.begin/end_host_upgrade`` 的领域异常映射为 HTTP，并写审计。

路由退化为 ``ok(acquire/release_agent_upgrade_gate(...))``。
"""

from __future__ import annotations

import secrets

from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.api.error_helpers import raise_api_http_error
from backend.core.audit import record_audit
from backend.services.host_maintenance import HostMaintenanceConflict
from backend.services.host_upgrade_gate import (
    HostAbortDrainTimeoutError,
    HostAbortPendingError,
    HostHasActiveJobsError,
    HostNotFoundError,
    HostRetiredError,
    begin_host_upgrade,
    end_host_upgrade,
)


# 鉴权沿用 agent secret（Ansible 侧已有 AGENT_SECRET）；窗口由调用方在升级完成后
# release，异常路径靠 maintenance_until TTL 过期兜底（与 UI 热更新同一实现）。


class UpgradeGateRequest(BaseModel):
    holder: str = ""
    abort_running_jobs: bool = False


class UpgradeGateReleaseRequest(BaseModel):
    holder: str = ""


def raise_upgrade_gate_http(host_id: str, exc: Exception) -> None:
    if isinstance(exc, HostNotFoundError):
        raise HTTPException(
            status_code=404,
            detail={"code": "HOST_NOT_FOUND", "message": str(exc)},
        ) from None
    if isinstance(exc, HostAbortPendingError):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "HOST_ABORT_PENDING",
                "message": (
                    f"Abort is still draining for {len(exc.active_jobs)} job(s) "
                    f"on host {host_id}. Retry in approximately "
                    f"{exc.retry_after_seconds}s."
                ),
                "active_jobs": exc.active_jobs,
                "retry_after_seconds": exc.retry_after_seconds,
            },
        ) from None
    if isinstance(exc, HostHasActiveJobsError):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "HOST_HAS_ACTIVE_JOBS",
                "message": (
                    f"Host {host_id} has {len(exc.active_jobs)} active job(s). "
                    "Retry with abort_running_jobs=true to abort then upgrade."
                ),
                "active_jobs": exc.active_jobs,
            },
        ) from None
    if isinstance(exc, HostRetiredError):
        # ADR-0038 D5：退役主机拒绝执行/配置类动作（升级门禁同族）
        raise HTTPException(
            status_code=409,
            detail={
                "code": "HOST_RETIRED",
                "message": (
                    f"Host {host_id} is retired; unretire it before upgrade "
                    "(ADR-0038 D5)."
                ),
            },
        ) from None
    if isinstance(exc, HostAbortDrainTimeoutError):
        raise HTTPException(
            status_code=504,
            detail={
                "code": "ABORT_DRAIN_TIMEOUT",
                "message": (
                    f"Aborted jobs but {len(exc.lingering_jobs)} job(s) on host "
                    f"{host_id} did not reach a terminal state in time. "
                    "Investigate the agent or retry."
                ),
                "lingering_jobs": exc.lingering_jobs,
                "abort_summary": exc.abort_summary,
            },
        ) from None
    if isinstance(exc, HostMaintenanceConflict):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "HOST_IN_MAINTENANCE",
                "message": (
                    f"Host {host_id} is already in a maintenance window "
                    "(another upgrade is in progress). Retry later."
                ),
            },
        ) from None
    raise exc


def acquire_agent_upgrade_gate(
    db: Session,
    host_id: str,
    payload: UpgradeGateRequest,
) -> dict:
    """外部升级入口申请维护窗口（#1249，agent secret 鉴权）。

    成功即持有窗口：期间该主机的派发与 claim 都被跳过（#960 互斥语义）。
    调用方必须在升级完成后调用 ``.../upgrade-gate/release``；进程崩溃等
    异常路径由维护窗口 TTL 过期兜底。
    """
    effective_holder = payload.holder.strip() or f"upgrade-gate:{secrets.token_hex(4)}"
    try:
        gate = begin_host_upgrade(
            db,
            host_id,
            holder=effective_holder,
            abort_running_jobs=payload.abort_running_jobs,
            triggered_by="agent-api",
        )
    except (
        HostNotFoundError,
        HostAbortPendingError,
        HostHasActiveJobsError,
        HostAbortDrainTimeoutError,
        HostMaintenanceConflict,
    ) as exc:
        raise_upgrade_gate_http(host_id, exc)

    record_audit(
        db,
        action="upgrade_gate_acquire",
        resource_type="host",
        resource_id=host_id,
        details={
            "holder": effective_holder,
            "abort_running_jobs": payload.abort_running_jobs,
            "active_before": len(gate["active_jobs"]),
            "aborted_jobs": (gate["aborted_summary"] or {}).get("aborted_jobs", []),
        },
        username="agent-api",
    )
    db.commit()
    return gate


def release_agent_upgrade_gate(
    db: Session,
    host_id: str,
    payload: UpgradeGateReleaseRequest,
) -> dict:
    """释放维护窗口；holder 不匹配时不误清他人窗口（幂等，可重复调用）。"""
    holder = payload.holder.strip()
    if not holder:
        raise_api_http_error(400, "HOLDER_REQUIRED", "holder must not be empty")
    end_host_upgrade(db, host_id, holder)
    record_audit(
        db,
        action="upgrade_gate_release",
        resource_type="host",
        resource_id=host_id,
        details={"holder": holder},
        username="agent-api",
    )
    db.commit()
    return {"host_id": host_id, "holder": holder, "released": True}


# 路由 / 既有测试用的私有名与端点别名。
_raise_upgrade_gate_http = raise_upgrade_gate_http
acquire_upgrade_gate = acquire_agent_upgrade_gate
release_upgrade_gate = release_agent_upgrade_gate
