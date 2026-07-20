"""SocketIO client for agent-to-backend real-time communication.

Replaces the legacy raw-WebSocket client with ``socketio.Client``
(sync mode) to match the Agent's threading model.

Features:
- Automatic reconnection (handled by python-socketio built-in)
- Auth handshake via ``auth`` dict on connect
- Message buffering during disconnect (up to MAX_BUFFER messages)
- StepLogger with local file + SocketIO dual output
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, Deque, Dict, Optional

logger = logging.getLogger(__name__)

try:
    import socketio as _sio_lib
    _HAS_SOCKETIO = True
except ImportError:
    _HAS_SOCKETIO = False
    logger.warning("python-socketio not installed, SocketIO log streaming disabled")

from .script_verifier import verify_scripts_payload


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.environ[key])
    except (KeyError, ValueError):
        return default


def _env_float(key: str, default: float) -> float:
    try:
        return float(os.environ[key])
    except (KeyError, ValueError):
        return default


class AgentSocketIOClient:
    """Synchronous SocketIO client for the agent process.

    Drop-in replacement for the legacy WebSocket client. The public API
    (connect, disconnect, send_log, send_step_update, send_heartbeat,
    connected property) is unchanged so callers need no modifications.

    ADR-0026 P2-2: ``send_log`` batches lines (size/time flush) and applies
    optional ``log_rate_limit`` backpressure before emit.
    """

    MAX_BUFFER = _env_int("WS_BUFFER_SIZE", 1000)
    MAX_RECONNECT_DELAY = _env_float("WS_RECONNECT_MAX_DELAY", 30.0)
    LOG_BATCH_MAX_LINES = _env_int("STP_LOG_BATCH_MAX_LINES", 50)
    LOG_BATCH_FLUSH_MS = _env_int("STP_LOG_BATCH_FLUSH_MS", 200)

    def __init__(self, api_url: str, host_id: int | str, agent_secret: str = ""):
        self._api_url = api_url
        self._host_id = host_id
        self._agent_secret = agent_secret
        self._connected = False
        self._buffer: Deque[dict] = deque(maxlen=self.MAX_BUFFER)
        self._lock = threading.Lock()
        self._seq_counters: Dict[int, int] = {}
        self._sio: Optional[Any] = None
        self._stop_event = threading.Event()
        self._control_handler: Optional[Any] = None
        # 审计 Agent #5: buffer 溢出丢弃计数,_emit/_replay_buffer prepend 路径共享。
        self._dropped_count = 0
        # ADR-0026 P2-2: per-job log batches + rate limit
        self._log_batches: Dict[int, list] = {}
        self._log_flush_timer: Optional[threading.Timer] = None
        self._log_rate_limit: Optional[int] = None
        self._log_rate_count = 0
        self._log_rate_window_start = time.monotonic()
        self._log_rate_dropped = 0

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def dropped_count(self) -> int:
        """Number of buffer-overflow drops since process start.

        审计 Agent #5: 暴露给 heartbeat stats 或外部监控,区分"buffer 内积压"与"实际丢消息"。
        """
        return self._dropped_count

    def reset_dropped_count(self) -> int:
        """Read and reset ``_dropped_count``. Used by heartbeat to report delta-since-last."""
        with self._lock:
            v = self._dropped_count
            self._dropped_count = 0
            return v

    def set_control_handler(self, handler) -> None:
        """Register a callback for 'control' events from the server."""
        self._control_handler = handler

    def _build_url(self) -> str:
        """Return the base HTTP(S) URL for SocketIO (it upgrades internally)."""
        return self._api_url.rstrip("/")

    def connect(self) -> bool:
        """Attempt to connect and authenticate. Returns True on success."""
        if not _HAS_SOCKETIO:
            return False

        try:
            sio = _sio_lib.Client(
                reconnection=False,
                logger=False,
                engineio_logger=False,
            )

            @sio.on("connect", namespace="/agent")
            def _on_connect():
                self._connected = True
                logger.info("sio_agent_connected host_id=%s", self._host_id)
                self._replay_buffer()

            @sio.on("disconnect", namespace="/agent")
            def _on_disconnect():
                self._connected = False
                logger.info("sio_agent_disconnected host_id=%s", self._host_id)

            @sio.on("control", namespace="/agent")
            def _on_control(data):
                logger.info("sio_control_received: %s", data)
                if self._control_handler:
                    try:
                        self._control_handler(data)
                    except Exception as e:
                        logger.warning("sio_control_handler_error: %s", e)

            @sio.on("verify_scripts", namespace="/agent")
            def _on_verify_scripts(data):
                """ADR-0021 dispatch gate: server requests sha256 verification.

                Returning a dict from a sync ``socketio.Client`` handler
                automatically forwards it as the ack payload.
                """
                try:
                    payload = data or {}
                    expected = payload.get("expected") or []
                    return verify_scripts_payload(
                        expected,
                        host_id=str(self._host_id),
                    )
                except Exception as exc:
                    logger.exception("sio_verify_scripts_failed: %s", exc)
                    return {
                        "host_id": str(self._host_id),
                        "agent_version": os.getenv("STP_AGENT_VERSION", "unknown"),
                        "results": [],
                        "error": f"handler_exception: {exc}",
                    }

            self._sio = sio
            url = self._build_url()
            sio.connect(
                url,
                namespaces=["/agent"],
                auth={
                    "agent_secret": self._agent_secret,
                    "host_id": str(self._host_id),
                },
                wait_timeout=10,
            )
            return self._connected

        except Exception as e:
            logger.warning("sio_connect_failed: %s", e)
            self._connected = False
            return False

    def disconnect(self):
        """Gracefully close the SocketIO connection and stop reconnect loop."""
        self.flush_logs()
        self._stop_event.set()
        self._connected = False
        if self._sio:
            try:
                self._sio.disconnect()
            except Exception:
                pass
            self._sio = None

    def start_reconnect_loop(self):
        """Start a background daemon thread that reconnects on disconnect."""
        self._stop_event.clear()
        t = threading.Thread(target=self._reconnect_loop, daemon=True, name="sio-reconnect")
        t.start()
        logger.info("sio_reconnect_loop_started")

    def _reconnect_loop(self):
        backoff = 1.0
        while not self._stop_event.is_set():
            if not self._connected:
                if self._sio:
                    try:
                        self._sio.disconnect()
                    except Exception:
                        pass
                    self._sio = None
                logger.info("sio_reconnecting in %.1fs...", backoff)
                self._stop_event.wait(backoff)
                if self._stop_event.is_set():
                    break
                success = self.connect()
                if success:
                    backoff = 1.0
                else:
                    backoff = min(backoff * 2, self.MAX_RECONNECT_DELAY)
            else:
                backoff = 1.0
                self._stop_event.wait(5.0)

    def _replay_buffer(self):
        """Replay buffered messages after reconnection.

        审计 Agent #5: 三段式保证顺序与并发安全。
        Why: 旧实现 popleft → emit → 失败 appendleft 与并发 ``_emit.append`` 互相穿插,
            重连失败时 buffer 顺序可能错乱(部分 drained items prepend 回头,新 append 的
            消息穿插其间)。
        How to apply:
            1. 锁内一次性 drain 到本地 list 并清空 buffer
            2. 释放锁后按 FIFO 逐条 emit;期间并发 ``_emit`` 仍可正常进入 buffer 尾
            3. emit 失败时加锁,把未发送的 drained 切片整体 prepend 回 buffer 头
        """
        with self._lock:
            if not self._buffer:
                return
            drained: list[dict] = list(self._buffer)
            self._buffer.clear()
        count = len(drained)
        logger.info("sio_replaying %d buffered messages", count)
        replayed = 0
        for idx, msg in enumerate(drained):
            event = msg.pop("_event", "step_log")
            try:
                self._sio.emit(event, msg, namespace="/agent")
                replayed += 1
            except Exception as e:
                # 失败:把 (失败的 msg + drained[idx+1:]) prepend 回 buffer 头,顺序不变。
                msg["_event"] = event
                with self._lock:
                    self._prepend_locked([msg] + drained[idx + 1:])
                logger.warning("sio_replay_failed after %d messages: %s", replayed, e)
                self._connected = False
                return
        logger.info("sio_replayed %d messages", replayed)

    def _prepend_locked(self, items: list[dict]) -> None:
        """Prepend ``items`` to ``_buffer`` head preserving original order.

        必须在持有 ``self._lock`` 时调用。``deque(maxlen=N).appendleft`` 满载时丢右端(最新),
        每次溢出自增 ``_dropped_count``,首次和每 100 次触发周期 warning。
        """
        for m in reversed(items):
            if len(self._buffer) >= self.MAX_BUFFER:
                self._dropped_count += 1
                if self._dropped_count == 1 or self._dropped_count % 100 == 0:
                    logger.warning(
                        "sio_buffer_overflow_dropped count=%d max=%d",
                        self._dropped_count, self.MAX_BUFFER,
                    )
            self._buffer.appendleft(m)

    def _next_seq(self, run_id: int) -> int:
        with self._lock:
            seq = self._seq_counters.get(run_id, 0) + 1
            self._seq_counters[run_id] = seq
            return seq

    def _emit(self, event: str, data: dict) -> bool:
        """Emit an event. Buffers if disconnected. Returns True if sent immediately."""
        with self._lock:
            if self._connected and self._sio:
                try:
                    self._sio.emit(event, data, namespace="/agent")
                    return True
                except Exception as e:
                    logger.warning("sio_emit_failed: %s", e)
                    self._connected = False
                    data["_event"] = event
                    self._append_locked(data)
                    return False
            else:
                data["_event"] = event
                self._append_locked(data)
                return False

    def _append_locked(self, data: dict) -> None:
        """Append to ``_buffer`` tail. 必须在持有 ``self._lock`` 时调用。

        审计 Agent #5: ``deque(maxlen=N).append`` 满载时丢左端(最旧),每次溢出自增
        ``_dropped_count`` 并周期 warning。
        """
        if len(self._buffer) >= self.MAX_BUFFER:
            self._dropped_count += 1
            if self._dropped_count == 1 or self._dropped_count % 100 == 0:
                logger.warning(
                    "sio_buffer_overflow_dropped count=%d max=%d",
                    self._dropped_count, self.MAX_BUFFER,
                )
        self._buffer.append(data)

    def send(self, message: dict) -> bool:
        """Legacy compatibility: route to appropriate emit based on message type."""
        msg_type = message.get("type", "")
        if msg_type == "log":
            return self._emit("step_log", message)
        elif msg_type == "step_update":
            return self._emit("step_update", message)
        elif msg_type == "heartbeat":
            return self._emit("heartbeat", message)
        else:
            return self._emit(msg_type or "message", message)

    def set_log_rate_limit(self, limit: Optional[int]) -> None:
        """Max log lines accepted per second (None = unlimited). Excess dropped."""
        with self._lock:
            self._log_rate_limit = limit
            self._log_rate_count = 0
            self._log_rate_window_start = time.monotonic()
        action = f"{limit} lines/s" if limit is not None else "unlimited"
        logger.info("sio_log_rate_limit: %s", action)

    def send_log(self, run_id: int, step_id: int | str, level: str, msg: str) -> bool:
        """Queue a log line; flush by batch size / timer (ADR-0026 P2-2)."""
        should_flush = False
        with self._lock:
            if not self._allow_log_locked():
                self._log_rate_dropped += 1
                if self._log_rate_dropped == 1 or self._log_rate_dropped % 100 == 0:
                    logger.warning(
                        "sio_log_rate_dropped count=%d limit=%s",
                        self._log_rate_dropped, self._log_rate_limit,
                    )
                return False
            seq = self._seq_counters.get(run_id, 0) + 1
            self._seq_counters[run_id] = seq
            line = {
                "step_id": step_id,
                "seq": seq,
                "level": level,
                "ts": datetime.now(timezone.utc).isoformat() + "Z",
                "msg": msg,
            }
            batch = self._log_batches.setdefault(int(run_id), [])
            batch.append(line)
            if len(batch) >= max(1, self.LOG_BATCH_MAX_LINES):
                should_flush = True
            elif self._log_flush_timer is None:
                delay = max(1, self.LOG_BATCH_FLUSH_MS) / 1000.0
                self._log_flush_timer = threading.Timer(delay, self._flush_logs_timer)
                self._log_flush_timer.daemon = True
                self._log_flush_timer.start()
        if should_flush:
            return self.flush_logs(run_id=int(run_id))
        return True

    def _allow_log_locked(self) -> bool:
        """Token-bucket style 1s window. Caller must hold ``_lock``."""
        limit = self._log_rate_limit
        if limit is None:
            return True
        if limit <= 0:
            return False
        now = time.monotonic()
        if now - self._log_rate_window_start >= 1.0:
            self._log_rate_window_start = now
            self._log_rate_count = 0
        if self._log_rate_count >= limit:
            return False
        self._log_rate_count += 1
        return True

    def _flush_logs_timer(self) -> None:
        with self._lock:
            self._log_flush_timer = None
        self.flush_logs()

    def flush_logs(self, run_id: Optional[int] = None) -> bool:
        """Flush pending log batches (all jobs, or one job)."""
        with self._lock:
            if self._log_flush_timer is not None and run_id is None:
                try:
                    self._log_flush_timer.cancel()
                except Exception:
                    pass
                self._log_flush_timer = None
            if run_id is None:
                pending = self._log_batches
                self._log_batches = {}
            else:
                lines = self._log_batches.pop(int(run_id), [])
                pending = {int(run_id): lines} if lines else {}
        ok = True
        for jid, lines in pending.items():
            if not lines:
                continue
            if not self._emit("step_log", {
                "run_id": jid,
                "job_id": jid,
                "lines": lines,
            }):
                ok = False
        return ok

    def send_step_update(self, run_id: int, step_id: int | str, status: str, **kwargs) -> bool:
        """Send a step status update."""
        data: dict = {
            "run_id": run_id,
            "job_id": run_id,
            "step_id": step_id,
            "status": status,
        }
        for key in ("started_at", "finished_at", "exit_code", "error_message", "progress"):
            if key in kwargs and kwargs[key] is not None:
                val = kwargs[key]
                if isinstance(val, datetime):
                    val = val.isoformat() + "Z"
                data[key] = val
        return self._emit("step_update", data)

    def send_heartbeat(self, stats: dict | None = None) -> bool:
        """Send a heartbeat message via SocketIO."""
        return self._emit("heartbeat", {
            "host_id": self._host_id,
            "stats": stats or {},
        })


class StepLogger:
    """Per-step logger that sends log lines via SocketIO (or falls back to buffer).
    Also optionally writes logs to a local file.
    """

    def __init__(self, sio_client: AgentSocketIOClient, run_id: int, step_id: int | str, log_file: Optional[str] = None):
        self._sio = sio_client
        self._run_id = run_id
        self._step_id = step_id
        self._log_file = log_file
        self._line_count = 0

        if self._log_file:
            try:
                os.makedirs(os.path.dirname(self._log_file), exist_ok=True)
            except Exception as e:
                logger.warning("Failed to create log directory for %s: %s", self._log_file, e)

    @property
    def line_count(self) -> int:
        return self._line_count

    def log(self, message: str, level: str = "INFO"):
        self._line_count += 1
        ts = datetime.now(timezone.utc).isoformat() + "Z"

        self._sio.send_log(self._run_id, self._step_id, level, message)

        if self._log_file:
            try:
                with open(self._log_file, "a", encoding="utf-8") as f:
                    f.write(f"{ts} [{level}] {message}\n")
            except Exception as e:
                if self._line_count == 1:
                    logger.warning("Failed to write to log file %s: %s", self._log_file, e)

    def info(self, message: str):
        self.log(message, "INFO")

    def warn(self, message: str):
        self.log(message, "WARN")

    def error(self, message: str):
        self.log(message, "ERROR")

    def debug(self, message: str):
        self.log(message, "DEBUG")
