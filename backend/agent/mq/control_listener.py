"""Background listener for the stp:control Redis Stream.

Dispatches commands to MQProducer (backpressure) and ToolRegistry (tool_update).
Runs as a daemon thread; safe to start/stop from main thread.
"""

import logging
import threading
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from backend.agent.mq.producer import MQProducer
    from backend.agent.registry.tool_registry import ToolRegistry

logger = logging.getLogger(__name__)

CONTROL_STREAM = "stp:control"
CONTROL_GROUP = "agent-consumer"
BLOCK_MS = 2000


class ControlListener:
    """Reads stp:control and applies backpressure / tool-update commands."""

    def __init__(
        self,
        redis_url: str,
        host_id: str,
        mq_producer: "MQProducer",
        tool_registry: Optional["ToolRegistry"] = None,
    ):
        self._host_id = host_id
        self._mq_producer = mq_producer
        self._tool_registry = tool_registry
        self._consumer_name = f"agent-{host_id}"
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._redis = None

        if not mq_producer.connected:
            return

        try:
            import redis
            self._redis = redis.Redis.from_url(redis_url, decode_responses=True,
                                               socket_connect_timeout=5, socket_timeout=5)
            # Create consumer group, starting from new messages ($)
            try:
                self._redis.xgroup_create(CONTROL_STREAM, CONTROL_GROUP, id="$", mkstream=True)
            except Exception:
                pass  # Already exists
        except Exception as e:
            logger.warning(f"ControlListener Redis init failed: {e}")
            self._redis = None

    def start(self) -> None:
        if self._redis is None:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="mq-control-listener"
        )
        self._thread.start()
        logger.info("MQ control listener started")

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        if self._redis:
            try:
                self._redis.close()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                results = self._redis.xreadgroup(
                    groupname=CONTROL_GROUP,
                    consumername=self._consumer_name,
                    streams={CONTROL_STREAM: ">"},
                    count=10,
                    block=BLOCK_MS,
                )
                if results:
                    for _stream_name, messages in results:
                        for msg_id, fields in messages:
                            self._handle(msg_id, fields)
            except Exception as e:
                if not self._stop_event.is_set():
                    logger.warning(f"ControlListener read error: {e}")
                    self._stop_event.wait(2.0)

    def _handle(self, msg_id: str, fields: dict) -> None:
        target = fields.get("target_host_id", "*")
        if target != "*" and target != self._host_id:
            self._ack(msg_id)
            return

        command = fields.get("command")
        if command == "backpressure":
            limit_str = fields.get("log_rate_limit")
            limit: Optional[int] = None
            if limit_str and limit_str not in ("None", "null", ""):
                try:
                    limit = int(limit_str)
                except ValueError:
                    pass
            self._mq_producer.set_log_rate_limit(limit)

        elif command == "tool_update":
            if self._tool_registry is not None:
                try:
                    tool_id = int(fields.get("tool_id", 0))
                    version = fields.get("version", "")
                except (TypeError, ValueError):
                    tool_id, version = 0, ""
                if tool_id:
                    threading.Thread(
                        target=self._tool_registry.pull_tool_sync,
                        args=(tool_id, version),
                        daemon=True,
                        name=f"tool-pull-{tool_id}",
                    ).start()
        else:
            logger.warning(f"ControlListener: unknown command '{command}'")

        self._ack(msg_id)

    def _ack(self, msg_id: str) -> None:
        try:
            self._redis.xack(CONTROL_STREAM, CONTROL_GROUP, msg_id)
        except Exception as e:
            logger.warning(f"ControlListener ACK failed: {e}")
