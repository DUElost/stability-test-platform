"""Step trace local persistence (Phase B: Redis XADD removed).

After Phase 4, this module only writes step_trace to SQLite WAL.
HTTP upload is delegated to StepTraceUploader.
The log_rate_limit API is retained for SocketIO control command compatibility.
"""

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from backend.agent.registry.local_db import LocalDB

logger = logging.getLogger(__name__)


class MQProducer:
    """Local step-trace writer. Redis XADD removed in Phase 4.

    Retained as a thin wrapper around LocalDB for backward compatibility
    with callers (pipeline_engine, etc.) that still reference MQProducer.
    """

    def __init__(self, redis_url: str, host_id: str, local_db: Optional["LocalDB"] = None):
        self._host_id = host_id
        self._local_db = local_db
        self._log_rate_limit: Optional[int] = None
        self._log_count = 0
        self._rate_window_start = time.monotonic()
        self._lock = threading.Lock()

    @property
    def connected(self) -> bool:
        return True

    def set_log_rate_limit(self, limit: Optional[int]) -> None:
        """Set max log messages per second. None = unlimited."""
        with self._lock:
            self._log_rate_limit = limit
            self._log_count = 0
            self._rate_window_start = time.monotonic()
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
        """No-op: log streaming now handled by SocketIO ws_client."""
        return None

    def close(self) -> None:
        pass


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()
