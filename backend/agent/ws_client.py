"""SocketIO client for agent-to-backend real-time communication.

Replaces the legacy raw-WebSocket ``AgentWSClient`` with ``socketio.Client``
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


class AgentWSClient:
    """Synchronous SocketIO client for the agent process.

    Drop-in replacement for the legacy WebSocket client. The public API
    (connect, disconnect, send_log, send_step_update, send_heartbeat,
    connected property) is unchanged so callers need no modifications.
    """

    MAX_BUFFER = _env_int("WS_BUFFER_SIZE", 1000)
    MAX_RECONNECT_DELAY = _env_float("WS_RECONNECT_MAX_DELAY", 30.0)

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

    @property
    def connected(self) -> bool:
        return self._connected

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
        """Replay buffered messages after reconnection."""
        if not self._buffer:
            return
        count = len(self._buffer)
        logger.info("sio_replaying %d buffered messages", count)
        replayed = 0
        while self._buffer:
            msg = self._buffer.popleft()
            try:
                event = msg.pop("_event", "step_log")
                self._sio.emit(event, msg, namespace="/agent")
                replayed += 1
            except Exception as e:
                self._buffer.appendleft(msg)
                logger.warning("sio_replay_failed after %d messages: %s", replayed, e)
                self._connected = False
                return
        logger.info("sio_replayed %d messages", replayed)

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
                    self._buffer.append(data)
                    return False
            else:
                data["_event"] = event
                self._buffer.append(data)
                return False

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

    def send_log(self, run_id: int, step_id: int | str, level: str, msg: str) -> bool:
        """Send a log line message."""
        return self._emit("step_log", {
            "run_id": run_id,
            "job_id": run_id,
            "step_id": step_id,
            "seq": self._next_seq(run_id),
            "level": level,
            "ts": datetime.now(timezone.utc).isoformat() + "Z",
            "msg": msg,
        })

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

    def __init__(self, ws_client: AgentWSClient, run_id: int, step_id: int | str, log_file: Optional[str] = None):
        self._ws = ws_client
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

        self._ws.send_log(self._run_id, self._step_id, level, message)

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
