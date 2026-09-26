"""PlanRun 相关 SocketIO 失效事件（#1519 从 api.routes 下沉）。"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def emit_job_status_invalidation(
    run_id: int, job_id: int, status: str, reason: str
) -> None:
    """ADR-0021 C5c: notify the frontend that a job's row needs a refetch.

    Used by the sync manual-retry / manual-exit endpoints.  We deliberately
    use ``schedule_emit`` (thread-safe bridge) because these handlers run on
    sync sessions and must not await.  The payload mirrors the agent-emitted
    ``job_status`` event so the frontend's existing handler can reuse it as
    a pure invalidation hint — no DB state is conveyed in the payload.
    """
    try:
        from backend.realtime.socketio_server import schedule_emit
    except Exception:
        return
    try:
        schedule_emit(
            "job_status",
            {
                "type": "JOB_STATUS",
                "payload": {
                    "job_id": int(job_id),
                    "status": status,
                    "reason": reason,
                },
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
            namespace="/dashboard",
            room=f"plan_run:{run_id}",
        )
    except Exception:
        logger.debug("emit_job_status_invalidation_failed", exc_info=True)


def emit_plan_run_status(run_id: int, status: str) -> None:
    """父 Run 进入终态后的 ``plan_run_status`` 推送（线程安全，同步语境可调）。

    载荷与 ``realtime.socketio_server.broadcast_plan_run_status`` 逐字一致。
    ADR-0052 D1 起父终态由异步聚合者判定，``/complete`` 处的同名推送几乎
    不再命中，故由编排者在父终态**提交后**补发（#3244 复核）。
    """
    try:
        from backend.realtime.socketio_server import schedule_emit
    except Exception:
        return
    try:
        schedule_emit(
            "plan_run_status",
            {
                "type": "PLAN_RUN_STATUS",
                "payload": {"status": status},
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
            namespace="/dashboard",
            room=f"plan_run:{run_id}",
        )
    except Exception:
        logger.debug("emit_plan_run_status_failed", exc_info=True)
