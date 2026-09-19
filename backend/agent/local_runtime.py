"""Local SocketIO + SQLite / registry bootstrap extracted from ``main`` (#736).

Covers the P2-2a early-control queue, LocalDB/DLE bind, AEE migrate hook,
patrol checkpoint store, and script registry initialize. ``main`` keeps the
ordering around ADB reconcile and disk/watcher subsystems.
"""

from __future__ import annotations

import logging
import queue
from dataclasses import dataclass
from typing import Any, Callable

from .config import BASE_DIR
from .registry.local_db import LocalDB
from .registry.patrol_checkpoint_store import PatrolCycleCheckpointStore
from .registry.script_registry import ScriptRegistry
from .socketio_client import AgentSocketIOClient
from .startup_guards import migrate_legacy_aee_state_on_startup

try:
    from .aee.device_log_event_client import bind_local_db
except ImportError:  # pragma: no cover - flat deploy layout
    from agent.aee.device_log_event_client import bind_local_db

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LocalStores:
    """Initialized on-host persistent stores used by the claim / recovery plane."""

    local_db: LocalDB
    patrol_checkpoint_store: PatrolCycleCheckpointStore
    script_registry: ScriptRegistry


def connect_socketio_with_early_control(
    api_url: str,
    host_id: str,
    agent_secret: str,
) -> tuple[AgentSocketIOClient, "queue.Queue[dict]"]:
    """Connect SocketIO with a buffering control handler (P2-2a).

    Commands arriving before the real handler is registered are queued and
    replayed later via :func:`replay_early_control_commands`.
    """
    sio_client = AgentSocketIOClient(api_url, host_id, agent_secret)
    early_control_queue: "queue.Queue[dict]" = queue.Queue()

    def _early_control_handler(data: dict) -> None:
        early_control_queue.put(data)

    sio_client.set_control_handler(_early_control_handler)
    sio_client.connect()
    sio_client.start_reconnect_loop()
    return sio_client, early_control_queue


def initialize_local_stores(
    *,
    api_url: str,
    agent_secret: str,
) -> LocalStores:
    """Open LocalDB, bind DLE client, migrate AEE keys, init patrol + scripts."""
    local_db = LocalDB()
    db_path = str(BASE_DIR / "agent_state.db")
    local_db.initialize(db_path)
    bind_local_db(local_db)
    migrate_legacy_aee_state_on_startup(db_path)

    patrol_checkpoint_store = PatrolCycleCheckpointStore(
        BASE_DIR / "patrol_checkpoint.db"
    )
    patrol_checkpoint_store.initialize()

    script_registry = ScriptRegistry(local_db, api_url, agent_secret)
    script_registry.initialize()
    return LocalStores(
        local_db=local_db,
        patrol_checkpoint_store=patrol_checkpoint_store,
        script_registry=script_registry,
    )


def replay_early_control_commands(
    early_control_queue: "queue.Queue[dict]",
    handle_control: Callable[[dict], Any],
) -> None:
    """Drain the startup-window control queue into the real handler (P2-2a)."""
    while True:
        try:
            early_data = early_control_queue.get_nowait()
        except queue.Empty:
            break
        try:
            handle_control(early_data)
        except Exception:
            logger.exception(
                "early_control_replay_failed command=%s",
                early_data.get("command"),
            )
