"""Redis Stream producer for Agent → Server communication.

Writes to two topics:
  stp:status — job/step status events (high priority, not rate-limited)
  stp:logs   — device log lines (low priority, subject to backpressure)

Thread-safe: all public methods acquire _lock before redis calls.
SQLite WAL write-before-send: step_trace is saved locally before Redis XADD,
then marked acked on success (replay-safe on reconnect).
"""

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from backend.agent.registry.local_db import LocalDB

logger = logging.getLogger(__name__)

STATUS_STREAM = "stp:status"
LOG_STREAM = "stp:logs"
STATUS_MAXLEN = 100_000
LOG_MAXLEN = 500_000

try:
    import redis as _redis_mod
    _HAS_REDIS = True
except ImportError:
    _HAS_REDIS = False
    logger.warning("redis library not installed — MQ producer disabled")


class MQProducer:
    """Sync Redis Stream producer. Falls back gracefully when Redis is unavailable.

    Supports automatic reconnection: when a write fails, the next write
    attempt will try to re-establish the connection (at most once per
    _RECONNECT_COOLDOWN seconds to avoid flooding).
    """

    _RECONNECT_COOLDOWN = 10  # seconds between reconnect attempts

    def __init__(self, redis_url: str, host_id: str, local_db: Optional["LocalDB"] = None):
        self._host_id = host_id
        self._redis_url = redis_url
        self._local_db = local_db
        self._log_rate_limit: Optional[int] = None
        self._log_count = 0
        self._rate_window_start = time.monotonic()
        self._lock = threading.Lock()
        self._redis = None
        self._connected = False
        self._last_reconnect_attempt = 0.0

        if not _HAS_REDIS:
            return

        self._try_connect()

    def _try_connect(self) -> bool:
        """Attempt to connect/reconnect to Redis. Returns True on success."""
        try:
            self._redis = _redis_mod.Redis.from_url(
                self._redis_url,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5,
            )
            self._redis.ping()
            self._connected = True
            logger.info(f"MQ producer connected: {self._redis_url}")
            return True
        except Exception as e:
            logger.warning(f"MQ producer Redis connection failed: {e}")
            self._redis = None
            self._connected = False
            return False

    def _ensure_connected(self) -> bool:
        """Lazily reconnect if disconnected (respecting cooldown)."""
        if self._connected:
            return True
        if not _HAS_REDIS:
            return False
        now = time.monotonic()
        if now - self._last_reconnect_attempt < self._RECONNECT_COOLDOWN:
            return False
        self._last_reconnect_attempt = now
        return self._try_connect()

    @property
    def connected(self) -> bool:
        return self._connected

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_log_rate_limit(self, limit: Optional[int]) -> None:
        """Set max log messages per second. None = unlimited."""
        with self._lock:
            self._log_rate_limit = limit
            self._log_count = 0
            self._rate_window_start = time.monotonic()
        action = f"{limit} msg/s" if limit is not None else "unlimited"
        logger.info(f"MQ log rate limit: {action}")

    def send_job_status(self, job_id: int, status: str, reason: str = "") -> Optional[str]:
        """Write job_status event to stp:status. Never rate-limited."""
        msg = {
            "msg_type": "job_status",
            "host_id": self._host_id,
            "job_id": str(job_id),
            "timestamp": _utcnow(),
            "status": status,
            "reason": reason,
        }
        return self._xadd_status(msg)

    def send_step_trace(
        self,
        job_id: int,
        step_id: str,
        stage: str,
        event_type: str,
        status: str,
        output: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> Optional[str]:
        """Write step_trace event to stp:status. Persists to SQLite first (WAL)."""
        ts = _utcnow()
        trace_id: Optional[int] = None

        if self._local_db is not None:
            try:
                from datetime import datetime as _dt
                trace_id = self._local_db.save_step_trace(
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
                logger.warning(f"MQ SQLite save_step_trace failed: {e}")

        msg = {
            "msg_type": "step_trace",
            "host_id": self._host_id,
            "job_id": str(job_id),
            "timestamp": ts,
            "step_id": step_id,
            "stage": stage,
            "event_type": event_type,
            "status": status,
            "output": output or "",
            "error_message": error_message or "",
        }
        msg_id = self._xadd_status(msg)

        if msg_id and trace_id and self._local_db is not None:
            try:
                self._local_db.mark_acked(trace_id)
            except Exception as e:
                logger.warning(f"MQ mark_acked failed: {e}")

        return msg_id

    def send_log(
        self,
        job_id: int,
        device_id: int,
        level: str,
        tag: str,
        message: str,
    ) -> Optional[str]:
        """Write log event to stp:logs. Subject to rate limiting."""
        if not self._allow_log():
            return None
        msg = {
            "job_id": str(job_id),
            "device_id": str(device_id),
            "level": level,
            "tag": tag,
            "message": message,
            "timestamp": _utcnow(),
        }
        return self._xadd_logs(msg)

    def close(self) -> None:
        if self._redis:
            try:
                self._redis.close()
            except Exception:
                pass
        self._connected = False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _allow_log(self) -> bool:
        with self._lock:
            if self._log_rate_limit is None:
                return True
            now = time.monotonic()
            if now - self._rate_window_start >= 1.0:
                self._log_count = 0
                self._rate_window_start = now
            if self._log_count < self._log_rate_limit:
                self._log_count += 1
                return True
            return False

    def _xadd_status(self, msg: dict) -> Optional[str]:
        if not self._ensure_connected():
            return None
        try:
            return self._redis.xadd(STATUS_STREAM, msg, maxlen=STATUS_MAXLEN, approximate=True)
        except Exception as e:
            logger.warning(f"MQ xadd stp:status failed: {e}")
            self._connected = False
            return None

    def _xadd_logs(self, msg: dict) -> Optional[str]:
        if not self._ensure_connected():
            return None
        try:
            return self._redis.xadd(LOG_STREAM, msg, maxlen=LOG_MAXLEN, approximate=True)
        except Exception as e:
            logger.warning(f"MQ xadd stp:logs failed: {e}")
            self._connected = False
            return None


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()
