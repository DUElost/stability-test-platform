"""Step trace local persistence (Phase B: Redis XADD removed).

After Phase 4, this module only writes step_trace to SQLite WAL.
HTTP upload is delegated to StepTraceUploader.

ADR-0026 P2-2: ``send_log`` forwards to ``AgentSocketIOClient`` (batched +
rate-limited) when bound; otherwise remains a no-op.
"""

import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from backend.agent.registry.local_db import LocalDB

logger = logging.getLogger(__name__)

_STEP_LOG_STREAM = os.getenv("STP_STEP_LOG_STREAM", "1").strip().lower() not in (
    "0", "false", "no", "off",
)


class StepTraceWriter:
    """Local step-trace writer — persists to SQLite WAL via LocalDB.

    Formerly MQProducer (Redis XADD removed in Phase 4). Renamed to
    reflect the actual storage backend: no message queue is involved.
    """

    def __init__(self, redis_url: str, host_id: str, local_db: Optional["LocalDB"] = None):
        self._host_id = host_id
        self._local_db = local_db
        self._log_rate_limit: Optional[int] = None
        self._log_count = 0
        self._rate_window_start = time.monotonic()
        self._lock = threading.Lock()
        self._sio_client: Optional[Any] = None

    def bind_sio_client(self, sio_client: Any) -> None:
        """Attach SocketIO client for real-time step_log streaming (P2-2)."""
        self._sio_client = sio_client
        if self._log_rate_limit is not None and hasattr(sio_client, "set_log_rate_limit"):
            sio_client.set_log_rate_limit(self._log_rate_limit)

    @property
    def connected(self) -> bool:
        return True

    def set_log_rate_limit(self, limit: Optional[int]) -> None:
        """Set max log messages per second. None = unlimited."""
        with self._lock:
            self._log_rate_limit = limit
            self._log_count = 0
            self._rate_window_start = time.monotonic()
        if self._sio_client is not None and hasattr(self._sio_client, "set_log_rate_limit"):
            self._sio_client.set_log_rate_limit(limit)
        action = f"{limit} msg/s" if limit is not None else "unlimited"
        logger.info("log_rate_limit: %s", action)

    def send_job_status(self, job_id: int, status: str, reason: str = "") -> Optional[str]:
        """No-op: job terminal status is sent via HTTP complete_job."""
        return None

    def send_step_trace(
        self,
        job_id: int,
        step_id: str,
        stage: str,
        event_type: str,
        status: str,
        output: Optional[str] = None,
        error_message: Optional[str] = None,
        fencing_token: str = "",
        trace_event_id: str = "",
    ) -> Optional[int]:
        """Save step_trace to SQLite WAL only. HTTP upload delegated to StepTraceUploader."""
        ts = _utcnow()
        if self._local_db is not None:
            try:
                from datetime import datetime as _dt
                return self._local_db.save_step_trace(
                    job_id=job_id,
                    step_id=step_id,
                    stage=stage,
                    event_type=event_type,
                    status=status,
                    output=output,
                    error_message=error_message,
                    original_ts=_dt.fromisoformat(ts.replace("Z", "+00:00")),
                    fencing_token=fencing_token,
                    trace_event_id=trace_event_id,
                )
            except Exception as e:
                logger.warning("SQLite save_step_trace failed: %s", e)
        return None

    def send_log(
        self,
        job_id: int,
        device_id: int,
        level: str,
        tag: str,
        message: str,
    ) -> Optional[str]:
        """Forward to SocketIO batcher when streaming enabled and client bound."""
        del device_id  # reserved for future device-scoped rooms
        if not _STEP_LOG_STREAM or self._sio_client is None:
            return None
        try:
            ok = self._sio_client.send_log(job_id, tag, level, message)
            return "ok" if ok else None
        except Exception as e:
            logger.debug("sio_send_log_failed: %s", e)
            return None

    def close(self) -> None:
        if self._sio_client is not None and hasattr(self._sio_client, "flush_logs"):
            try:
                self._sio_client.flush_logs()
            except Exception:
                logger.debug("sio_flush_logs_on_close_failed", exc_info=True)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()
