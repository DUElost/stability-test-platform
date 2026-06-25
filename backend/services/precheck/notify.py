"""SocketIO invalidation hints for dispatch gate progress."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def emit_dispatch_gate_invalidation(
    plan_run_id: int,
    *,
    phase: str | None = None,
    dispatch_status: str | None = None,
) -> None:
    try:
        from backend.realtime.socketio_server import schedule_emit
    except Exception:
        return
    try:
        schedule_emit(
            "precheck_update",
            {
                "type": "PRECHECK_UPDATE",
                "payload": {
                    "phase": phase,
                    "dispatch_status": dispatch_status,
                },
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
            namespace="/dashboard",
            room=f"plan_run:{plan_run_id}",
        )
    except Exception:
        logger.debug("emit_dispatch_gate_invalidation_failed", exc_info=True)


_emit_dispatch_gate_invalidation = emit_dispatch_gate_invalidation
