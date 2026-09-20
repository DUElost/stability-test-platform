"""#739：`on_step_log` 服务端摄取契约（自 agent 套件迁出的控制面侧用例）。

这两条测的是**控制面**的 socketio handler（`backend.realtime.socketio_server`），
原先挂在 `backend/agent/tests/` 下——agent 套件因此必须 import 控制面
（见 `tests/test_agent_test_import_ratchet.py` 的存量台账）。路由（Agent → 控制面）
与批量上送的 agent 侧行为仍在 `backend/agent/tests/test_step_log_batching.py` 测。
"""

from __future__ import annotations

import pytest


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
    # #2400：落盘是唯一去向——原先每行还向 job:/run: 两个无订阅方的房间双投，
    # 现在不再有任何推送（要恢复推送须同时接上订阅端，见
    # tests/test_realtime_wiring_contract.py）。
    assert emitted == []


@pytest.mark.asyncio
async def test_on_step_log_rejects_legacy_single_line(monkeypatch):
    from backend.realtime import socketio_server as sio_mod

    written = []

    async def fake_append(job_id, lines):
        written.append((job_id, list(lines)))

    monkeypatch.setattr(
        "backend.realtime.log_writer.append_log_lines", fake_append,
    )

    ns = sio_mod.AgentNamespace("/agent")
    await ns.on_step_log("sid", {
        "job_id": 42,
        "step_id": "s1",
        "seq": 1,
        "level": "INFO",
        "ts": "t1",
        "msg": "legacy",
    })

    assert written == []
