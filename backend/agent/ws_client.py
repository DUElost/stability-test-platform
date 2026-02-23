"""WebSocket client for agent-to-backend real-time communication.

Provides persistent WebSocket connection with:
- Authentication handshake via AGENT_SECRET
- Exponential backoff reconnection (1s -> 30s cap)
- Message buffering during disconnect (up to 1000 messages)
- Sequence-numbered message replay on reconnect
- HTTP heartbeat fallback when WS is unavailable
"""

import json
import logging
import os
import threading
import time
from collections import deque
from datetime import datetime
from typing import Any, Deque, Dict, Optional

logger = logging.getLogger(__name__)

try:
    import websockets
    from websockets.sync.client import connect as ws_connect
    _HAS_WEBSOCKETS = True
except ImportError:
    _HAS_WEBSOCKETS = False
    logger.warning("websockets library not installed, WebSocket log streaming disabled")


class AgentWSClient:
    """Synchronous WebSocket client for the agent process."""

    MAX_BUFFER = 1000
    INITIAL_BACKOFF = 1.0
    MAX_BACKOFF = 30.0
    PING_INTERVAL = 30
    PONG_TIMEOUT = 10

    def __init__(self, api_url: str, host_id: int, agent_secret: str = ""):
        self._api_url = api_url
        self._host_id = host_id
        self._agent_secret = agent_secret
        self._ws = None
        self._connected = False
        self._buffer: Deque[dict] = deque(maxlen=self.MAX_BUFFER)
        self._backoff = self.INITIAL_BACKOFF
        self._lock = threading.Lock()
        self._seq_counters: Dict[int, int] = {}  # run_id -> seq
        self._reconnect_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    @property
    def connected(self) -> bool:
        return self._connected

    def _build_ws_url(self) -> str:
        """Convert http(s) API URL to ws(s) WebSocket URL."""
        base = self._api_url.rstrip("/")
        if base.startswith("https://"):
            ws_base = "wss://" + base[len("https://"):]
        elif base.startswith("http://"):
            ws_base = "ws://" + base[len("http://"):]
        else:
            ws_base = "ws://" + base
        return f"{ws_base}/ws/agent/{self._host_id}"

    def connect(self) -> bool:
        """Attempt to connect and authenticate. Returns True on success."""
        if not _HAS_WEBSOCKETS:
            return False

        url = self._build_ws_url()
        try:
            self._ws = ws_connect(url, open_timeout=10, close_timeout=5)

            # Send auth message
            auth_msg = json.dumps({
                "type": "auth",
                "agent_secret": self._agent_secret,
            })
            self._ws.send(auth_msg)

            # Wait for auth_ack
            raw = self._ws.recv(timeout=10)
            resp = json.loads(raw)
            if resp.get("type") != "auth_ack":
                logger.warning(f"WS auth failed: unexpected response {resp}")
                self._ws.close()
                self._ws = None
                return False

            self._connected = True
            self._backoff = self.INITIAL_BACKOFF
            logger.info(f"WS connected to {url}")

            # Replay buffered messages
            self._replay_buffer()
            return True

        except Exception as e:
            logger.warning(f"WS connect failed: {e}")
            self._ws = None
            self._connected = False
            return False

    def disconnect(self):
        """Gracefully close the WebSocket connection and stop reconnect loop."""
        self._stop_event.set()
        self._connected = False
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None
        if self._reconnect_thread and self._reconnect_thread.is_alive():
            self._reconnect_thread.join(timeout=5)

    def reconnect(self) -> bool:
        """Attempt reconnection with exponential backoff."""
        self._connected = False
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None
        logger.info(f"WS reconnecting in {self._backoff:.1f}s...")
        time.sleep(self._backoff)
        self._backoff = min(self._backoff * 2, self.MAX_BACKOFF)
        return self.connect()

    def start_reconnect_loop(self):
        """Start a background daemon thread that monitors connection and reconnects on disconnect."""
        if self._reconnect_thread and self._reconnect_thread.is_alive():
            return
        self._stop_event.clear()
        self._reconnect_thread = threading.Thread(target=self._reconnect_loop, daemon=True, name="ws-reconnect")
        self._reconnect_thread.start()
        logger.info("WS reconnect loop started")

    def _reconnect_loop(self):
        """Background loop: monitors connection state, reconnects with exponential backoff."""
        while not self._stop_event.is_set():
            if not self._connected:
                try:
                    success = self.reconnect()
                    if success:
                        logger.info("WS reconnected successfully")
                    # If failed, reconnect() already slept with backoff
                except Exception as e:
                    logger.warning(f"WS reconnect attempt failed: {e}")
            else:
                # Reset backoff when connected
                self._backoff = self.INITIAL_BACKOFF
            # Check every 5s when connected, backoff handles delay when disconnected
            if self._connected:
                self._stop_event.wait(5.0)

    def _replay_buffer(self):
        """Replay buffered messages after reconnection."""
        if not self._buffer:
            return
        count = len(self._buffer)
        logger.info(f"Replaying {count} buffered messages")
        replayed = []
        while self._buffer:
            msg = self._buffer.popleft()
            try:
                self._ws.send(json.dumps(msg))
                replayed.append(msg)
            except Exception as e:
                # Put back failed messages
                self._buffer.appendleft(msg)
                for m in reversed(replayed):
                    self._buffer.appendleft(m)
                logger.warning(f"Replay failed after {len(replayed)} messages: {e}")
                self._connected = False
                return
        logger.info(f"Replayed {count} messages successfully")

    def _next_seq(self, run_id: int) -> int:
        """Get next sequence number for a run (thread-safe via _lock)."""
        with self._lock:
            seq = self._seq_counters.get(run_id, 0) + 1
            self._seq_counters[run_id] = seq
            return seq

    def send(self, message: dict) -> bool:
        """Send a message. Buffers if disconnected. Returns True if sent immediately."""
        with self._lock:
            if self._connected and self._ws:
                try:
                    self._ws.send(json.dumps(message))
                    return True
                except Exception as e:
                    logger.warning(f"WS send failed: {e}")
                    self._connected = False
                    self._buffer.append(message)
                    return False
            else:
                dropped = len(self._buffer) >= self.MAX_BUFFER
                self._buffer.append(message)
                if dropped:
                    logger.warning(f"WS buffer overflow, oldest message dropped (buffer={len(self._buffer)})")
                return False

    def send_log(self, run_id: int, step_id: int, level: str, msg: str) -> bool:
        """Send a log line message."""
        return self.send({
            "type": "log",
            "run_id": run_id,
            "step_id": step_id,
            "seq": self._next_seq(run_id),
            "level": level,
            "ts": datetime.utcnow().isoformat() + "Z",
            "msg": msg,
        })

    def send_step_update(self, run_id: int, step_id: int, status: str, **kwargs) -> bool:
        """Send a step status update."""
        msg = {
            "type": "step_update",
            "run_id": run_id,
            "step_id": step_id,
            "status": status,
        }
        for key in ("started_at", "finished_at", "exit_code", "error_message", "progress"):
            if key in kwargs and kwargs[key] is not None:
                val = kwargs[key]
                if isinstance(val, datetime):
                    val = val.isoformat() + "Z"
                msg[key] = val
        return self.send(msg)

    def send_heartbeat(self, stats: dict = None) -> bool:
        """Send a heartbeat message via WebSocket."""
        return self.send({
            "type": "heartbeat",
            "host_id": self._host_id,
            "stats": stats or {},
        })


class StepLogger:
    """Per-step logger that sends log lines via WebSocket (or falls back to buffer)."""

    def __init__(self, ws_client: AgentWSClient, run_id: int, step_id: int):
        self._ws = ws_client
        self._run_id = run_id
        self._step_id = step_id
        self._line_count = 0

    @property
    def line_count(self) -> int:
        return self._line_count

    def log(self, message: str, level: str = "INFO"):
        """Log a single line. Sends via WS if connected."""
        self._line_count += 1
        self._ws.send_log(self._run_id, self._step_id, level, message)

    def info(self, message: str):
        self.log(message, "INFO")

    def warn(self, message: str):
        self.log(message, "WARN")

    def error(self, message: str):
        self.log(message, "ERROR")

    def debug(self, message: str):
        self.log(message, "DEBUG")
