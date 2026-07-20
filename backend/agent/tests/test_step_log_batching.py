"""ADR-0026 P2-2 — step_log batching + rate-limit backpressure."""

from __future__ import annotations

import time
from collections import deque
from unittest.mock import MagicMock

import pytest

from backend.agent.socketio_client import AgentSocketIOClient
from backend.agent.mq.producer import StepTraceWriter
from backend.api.routes.heartbeat import _suggested_log_rate_limit


def _make_client(**kwargs) -> AgentSocketIOClient:
    client = AgentSocketIOClient(
        api_url="http://127.0.0.1:8000",
        host_id="host-1",
        agent_secret="",
    )
    client._sio = MagicMock()
    client._connected = True
    client._buffer = deque(maxlen=1000)
    for key, value in kwargs.items():
        setattr(client, key, value)
    return client


def test_send_log_batches_by_size(monkeypatch):
    client = _make_client()
    client.LOG_BATCH_MAX_LINES = 3
    client.LOG_BATCH_FLUSH_MS = 60_000  # avoid timer flush in test

    assert client.send_log(10, "s1", "INFO", "a") is True
    assert client.send_log(10, "s1", "INFO", "b") is True
    assert client._sio.emit.call_count == 0

    assert client.send_log(10, "s1", "INFO", "c") is True
    assert client._sio.emit.call_count == 1
    event, payload = client._sio.emit.call_args.args[:2]
    assert event == "step_log"
    assert payload["job_id"] == 10
    assert [line["msg"] for line in payload["lines"]] == ["a", "b", "c"]
    assert len(client._log_batches.get(10, [])) == 0


def test_send_log_flush_all_jobs():
    client = _make_client()
    client.LOG_BATCH_MAX_LINES = 100
    client.LOG_BATCH_FLUSH_MS = 60_000

    client.send_log(1, "s", "INFO", "x")
    client.send_log(2, "s", "INFO", "y")
    assert client.flush_logs() is True
    assert client._sio.emit.call_count == 2
    jobs = sorted(c.args[1]["job_id"] for c in client._sio.emit.call_args_list)
    assert jobs == [1, 2]


def test_rate_limit_drops_excess_lines():
    client = _make_client()
    client.LOG_BATCH_MAX_LINES = 100
    client.LOG_BATCH_FLUSH_MS = 60_000
    client.set_log_rate_limit(2)

    assert client.send_log(1, "s", "INFO", "1") is True
    assert client.send_log(1, "s", "INFO", "2") is True
    assert client.send_log(1, "s", "INFO", "3") is False
    assert client._log_rate_dropped == 1
    assert sum(len(v) for v in client._log_batches.values()) == 2


def test_step_trace_writer_forwards_when_bound(monkeypatch):
    monkeypatch.setenv("STP_STEP_LOG_STREAM", "1")
    # Re-import flag is module-level; patch the module constant.
    import backend.agent.mq.producer as producer_mod
    monkeypatch.setattr(producer_mod, "_STEP_LOG_STREAM", True)

    sio = MagicMock()
    sio.send_log.return_value = True
    writer = StepTraceWriter("", "h1")
    writer.bind_sio_client(sio)
    assert writer.send_log(7, 0, "INFO", "step-a", "hello") == "ok"
    sio.send_log.assert_called_once_with(7, "step-a", "INFO", "hello")


def test_step_trace_writer_noop_when_stream_disabled(monkeypatch):
    import backend.agent.mq.producer as producer_mod
    monkeypatch.setattr(producer_mod, "_STEP_LOG_STREAM", False)

    sio = MagicMock()
    writer = StepTraceWriter("", "h1")
    writer.bind_sio_client(sio)
    assert writer.send_log(7, 0, "INFO", "step-a", "hello") is None
    sio.send_log.assert_not_called()


def test_suggested_log_rate_limit_scales(monkeypatch):
    monkeypatch.setattr("backend.api.routes.heartbeat.LOG_RATE_LIMIT_BASE", 200)
    monkeypatch.setattr("backend.api.routes.heartbeat.LOG_RATE_LIMIT_MIN", 20)
    assert _suggested_log_rate_limit(0) == 200
    assert _suggested_log_rate_limit(10) == 190
    assert _suggested_log_rate_limit(500) == 20


@pytest.mark.asyncio
async def test_on_step_log_accepts_batch(monkeypatch):
    from backend.realtime import socketio_server as sio_mod

    emitted = []

    class FakeSio:
        async def emit(self, event, payload, namespace=None, room=None):
            emitted.append((event, payload, room))

    written = []

    async def fake_append(job_id, lines):
        written.append((job_id, list(lines)))

    monkeypatch.setattr(sio_mod, "get_sio", lambda: FakeSio())
    monkeypatch.setattr(
        "backend.realtime.log_writer.append_log_lines", fake_append,
    )

    ns = sio_mod.AgentNamespace("/agent")
    await ns.on_step_log("sid", {
        "job_id": 42,
        "run_id": 42,
        "lines": [
            {"step_id": "s1", "seq": 1, "level": "INFO", "ts": "t1", "msg": "a"},
            {"step_id": "s1", "seq": 2, "level": "WARN", "ts": "t2", "msg": "b"},
        ],
    })

    assert len(written) == 1
    assert written[0][0] == 42
    assert [x["msg"] for x in written[0][1]] == ["a", "b"]
    # 2 lines × 2 rooms
    assert len(emitted) == 4
    assert all(e[0] == "step_log" for e in emitted)
    assert all(e[1]["type"] == "STEP_LOG" for e in emitted)
