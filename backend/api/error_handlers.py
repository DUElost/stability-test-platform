"""领域异常 → HTTP 的统一翻译层（#3295，C2 棘轮出口）。

services 抛 `backend.services.errors` 的领域异常（不 import fastapi），这里把语义
异常翻译回重构前的对外响应：状态码取异常类的语义归属，响应信封与 FastAPI 默认
`HTTPException` 渲染逐字一致（`{"detail": ...}`）。因此各端点的既有 API 测试
无需改动即可守住「响应不变」。

`detail` 由服务层原样携带（纯字符串，或端点既定的结构化 dict）；handler 不理解、
不重组其内容——这是「对外响应逐字不变」的前提。

升级门禁的 `Host*` 领域异常不走统一 handler：它们的 404/409/504 映射只对
``POST /agent/hosts/{id}/upgrade-gate`` 成立，`hosts.py` 与 `release` 路径各有
自己的处理/兜底方式。全局注册会把那些路径上今天表现为 500（透传到
global_exception_handler）的异常改写成 409/404——「对外响应逐字不变」禁止这种
顺带改写。因此映射函数与它的 `except` 元组放在一起、由该端点显式调用；元组与
映射分支的可达性由 `backend/tests/services/test_agent_upgrade_gate.py` 的 #2638
契约守住。
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from backend.services.errors import ServiceError
from backend.services.host_maintenance import HostMaintenanceConflict
from backend.services.host_upgrade_gate import (
    HostAbortDrainTimeoutError,
    HostAbortPendingError,
    HostHasActiveJobsError,
    HostNotFoundError,
    HostRetiredError,
)


def register_domain_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ServiceError)
    async def _domain_exception_handler(request, exc: ServiceError):
        return JSONResponse(status_code=exc.status, content={"detail": exc.detail})


# 端点的 except 元组必须与本文件 raise_upgrade_gate_http 映射的每一个领域异常
# 一一对应：少一个，那条 HTTP 分支就从唯一入参路径不可达，异常原样上抛成 500
# （#2638：退役主机申请升级窗口时 HostRetiredError 就是这样漏掉的——409
# HOST_RETIRED 早就写好了）。判据取运行时子类集合，见测试。
UPGRADE_GATE_DOMAIN_ERRORS = (
    HostNotFoundError,
    HostRetiredError,
    HostAbortPendingError,
    HostHasActiveJobsError,
    HostAbortDrainTimeoutError,
    HostMaintenanceConflict,
)


def raise_upgrade_gate_http(host_id: str, exc: Exception) -> None:
    """``host_upgrade_gate.begin_host_upgrade`` 的领域异常 → HTTP（#3295 自 services 移出）。"""
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

