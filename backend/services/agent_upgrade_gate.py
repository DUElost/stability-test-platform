"""Agent 升级门禁业务（#1520 垂直切片：agent_api upgrade-gate）。

``POST /hosts/{id}/upgrade-gate`` / ``.../release``：调
``host_upgrade_gate.begin/end_host_upgrade``，领域异常原样向上传播，
HTTP 映射在 api 层（``backend/api/error_handlers.py`` 的
``raise_upgrade_gate_http`` + ``UPGRADE_GATE_DOMAIN_ERRORS``，#3295 把它移出 services）。
成功路径在此写审计。路由退化为「调服务 → ok()」。
"""

from __future__ import annotations

import secrets

from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.core.audit import record_audit
from backend.services.errors import BadRequest
from backend.services.host_upgrade_gate import (
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


def acquire_agent_upgrade_gate(
    db: Session,
    host_id: str,
    payload: UpgradeGateRequest,
) -> dict:
    """外部升级入口申请维护窗口（#1249，agent secret 鉴权）。

    成功即持有窗口：期间该主机的派发与 claim 都被跳过（#960 互斥语义）。
    调用方必须在升级完成后调用 ``.../upgrade-gate/release``；进程崩溃等
    异常路径由维护窗口 TTL 过期兜底。

    ``begin_host_upgrade`` 的领域异常（Host* / HostMaintenanceConflict）
    不在这里翻译——路由端点的 ``except UPGRADE_GATE_DOMAIN_ERRORS`` 元组 +
    api 层 ``raise_upgrade_gate_http`` 负责 HTTP 映射。元组必须覆盖映射的每一个
    领域异常：少一个，那条 HTTP 分支就**从唯一入参路径不可达**，异常原样上抛成
    500（#2638：退役主机申请升级窗口时 ``HostRetiredError`` 就是这样漏掉的——
    409 ``HOST_RETIRED`` 早就写好了）。
    """
    effective_holder = payload.holder.strip() or f"upgrade-gate:{secrets.token_hex(4)}"
    gate = begin_host_upgrade(
        db,
        host_id,
        holder=effective_holder,
        abort_running_jobs=payload.abort_running_jobs,
        triggered_by="agent-api",
    )

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
        raise BadRequest({"code": "HOLDER_REQUIRED", "message": "holder must not be empty"})
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


# 路由 / 既有测试用的端点别名。
acquire_upgrade_gate = acquire_agent_upgrade_gate
release_upgrade_gate = release_agent_upgrade_gate
