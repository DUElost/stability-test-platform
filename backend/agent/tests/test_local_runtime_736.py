"""#736: local SocketIO + SQLite bootstrap extracted from main."""

from __future__ import annotations

import queue
from unittest.mock import MagicMock, patch

from backend.agent.local_runtime import (
    connect_socketio_with_early_control,
    initialize_local_stores,
    replay_early_control_commands,
)


def test_replay_early_control_drains_queue():
    q: queue.Queue[dict] = queue.Queue()
    q.put({"command": "a"})
    q.put({"command": "b"})
    handler = MagicMock()
    replay_early_control_commands(q, handler)
    assert handler.call_count == 2
    assert q.empty()


def test_replay_early_control_swallows_handler_errors():
    q: queue.Queue[dict] = queue.Queue()
    q.put({"command": "bad"})
    q.put({"command": "ok"})
    handler = MagicMock(side_effect=[RuntimeError("x"), None])
    replay_early_control_commands(q, handler)
    assert handler.call_count == 2
    assert q.empty()


def test_connect_socketio_with_early_control_wires_buffer():
    with patch("backend.agent.local_runtime.AgentSocketIOClient") as cls:
        client = MagicMock()
        cls.return_value = client
        sio, early_q = connect_socketio_with_early_control("http://x", "h", "secret")
    assert sio is client
    client.set_control_handler.assert_called_once()
    client.connect.assert_called_once()
    client.start_reconnect_loop.assert_called_once()
    # buffered handler should enqueue
    handler = client.set_control_handler.call_args.args[0]
    handler({"command": "reload_config"})
    assert early_q.get_nowait() == {"command": "reload_config"}


def test_initialize_local_stores_binds_and_migrates():
    with (
        patch("backend.agent.local_runtime.LocalDB") as db_cls,
        patch("backend.agent.local_runtime.bind_local_db") as bind,
        patch("backend.agent.local_runtime.migrate_legacy_aee_state_on_startup") as migrate,
        patch("backend.agent.local_runtime.PatrolCycleCheckpointStore") as patrol_cls,
        patch("backend.agent.local_runtime.ScriptRegistry") as reg_cls,
    ):
        db = MagicMock()
        db_cls.return_value = db
        patrol = MagicMock()
        patrol_cls.return_value = patrol
        reg = MagicMock()
        reg_cls.return_value = reg
        stores = initialize_local_stores(api_url="http://x", agent_secret="s")
    db.initialize.assert_called_once()
    bind.assert_called_once_with(db)
    migrate.assert_called_once()
    patrol.initialize.assert_called_once()
    reg.initialize.assert_called_once()
    assert stores.local_db is db
    assert stores.patrol_checkpoint_store is patrol
    assert stores.script_registry is reg


def test_main_wires_local_runtime():
    import backend.agent.agent_application as app
    from tools.dev.source_anchor import SourceGuard

    guard = SourceGuard.of_module(app).anchored(
        "connect_socketio_with_early_control("
    )
    guard.assert_absent(
        "early_control_replay_failed",
        why="#736 local_runtime 已抽出，编排层不得回潮 early-control 回放日志",
    )
    guard.assert_absent(
        "AgentSocketIOClient(",
        why="#736 SocketIO 连接已迁出编排层",
    )
