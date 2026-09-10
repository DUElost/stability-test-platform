"""#1042: DLE create failure persists register intent and retries idempotently."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from backend.agent.aee.device_log_event_client import DeviceLogEventClient, bind_local_db
from backend.agent.registry.local_db import LocalDB


@pytest.fixture
def local_db(tmp_path):
    db = LocalDB()
    db.initialize(str(tmp_path / "agent.db"))
    bind_local_db(db)
    yield db
    bind_local_db(None)
    db.close()


def test_create_local_event_enqueues_intent_on_http_failure(local_db, tmp_path):
    client = DeviceLogEventClient(
        api_url="http://cp", agent_secret="s", host_id="h1", local_db=local_db,
    )
    src = tmp_path / "evt"
    src.mkdir()
    resp = MagicMock(status_code=503, text="down")
    with patch("backend.agent.aee.device_log_event_client.requests.post", return_value=resp):
        event_id = client.create_local_event(
            serial="d1",
            platform="MTK",
            event_type="KE",
            event_subtype=None,
            detected_at=datetime.now(timezone.utc),
            device_timestamp=None,
            local_path=src,
            plan_run_id=9,
            job_id=3,
            link_signal_seq_no=1,
        )
    assert event_id is None
    pending = local_db.get_pending_dle_registers()
    assert len(pending) == 1
    assert pending[0]["payload"]["state"] == "LOCAL"
    assert pending[0]["payload"]["local_path"] == str(src)
    assert pending[0]["payload"]["id"] == pending[0]["event_id"]


def test_drain_register_outbox_acks_after_success(local_db, tmp_path):
    client = DeviceLogEventClient(
        api_url="http://cp", agent_secret="s", host_id="h1", local_db=local_db,
    )
    event_id = str(uuid4())
    payload = {
        "id": event_id,
        "serial": "d1",
        "platform": "MTK",
        "event_type": "KE",
        "detected_at": datetime.now(timezone.utc).isoformat(),
        "state": "LOCAL",
        "local_path": str(tmp_path / "x"),
        "host_id": "h1",
        "job_id": 1,
        "plan_run_id": 2,
        "link_signal_seq_no": 4,
    }
    local_db.enqueue_dle_register(event_id, payload)

    ok = MagicMock(status_code=200)
    ok.json.return_value = {"data": {"event_ids": [event_id]}}
    with patch("backend.agent.aee.device_log_event_client.requests.post", return_value=ok):
        assert client.drain_register_outbox() == 1
    assert local_db.get_pending_dle_registers() == []


def test_create_local_event_sends_client_id(local_db, tmp_path):
    client = DeviceLogEventClient(
        api_url="http://cp", agent_secret="s", host_id="h1", local_db=local_db,
    )
    src = tmp_path / "evt"
    src.mkdir()
    posted = []

    def fake_post(url, **kwargs):
        posted.append(kwargs.get("json"))
        resp = MagicMock(status_code=200)
        eid = kwargs["json"]["events"][0]["id"]
        resp.json.return_value = {"data": {"event_ids": [eid]}}
        return resp

    with patch("backend.agent.aee.device_log_event_client.requests.post", side_effect=fake_post):
        eid = client.create_local_event(
            serial="d1",
            platform="MTK",
            event_type="KE",
            event_subtype=None,
            detected_at=datetime.now(timezone.utc),
            device_timestamp=None,
            local_path=src,
            plan_run_id=1,
            job_id=2,
        )
    assert eid is not None
    assert posted[0]["events"][0]["id"] == eid


def test_drain_permanent_failure_dead_letters_then_replay(local_db, tmp_path):
    """#1204：持续 4xx 的 create 意图达阈值转死信；死信不再占 pending 窗口
    （队首饿死消除）；replay 后重新入窗并可成功补建。"""
    client = DeviceLogEventClient(
        api_url="http://cp", agent_secret="s", host_id="h1", local_db=local_db,
    )
    eid = str(uuid4())
    payload = {
        "id": eid,
        "serial": "d1",
        "platform": "MTK",
        "event_type": "KE",
        "detected_at": datetime.now(timezone.utc).isoformat(),
        "state": "LOCAL",
        "local_path": str(tmp_path / "x"),
        "host_id": "h1",
        "job_id": 1,
        "plan_run_id": 2,
        "link_signal_seq_no": 4,
    }
    local_db.enqueue_dle_register(eid, payload)

    rejected = MagicMock(status_code=403, text="host mismatch")
    with patch("backend.agent.aee.device_log_event_client.requests.post", return_value=rejected):
        for _ in range(10):
            assert client.drain_register_outbox() == 0

    # 达阈值 → 死信；acked=0 的死信行不再被取出（此前会永久占满队首）。
    assert local_db.get_pending_dle_registers() == []

    # 队首饿死消除：新意图仍出现在 pending 窗口并可被 drain。
    fresh_id = str(uuid4())
    local_db.enqueue_dle_register(fresh_id, {**payload, "id": fresh_id, "event_type": "NATIVE"})
    pending_ids = [p["event_id"] for p in local_db.get_pending_dle_registers()]
    assert pending_ids == [fresh_id]

    # 死信回放：重置 dead_letter/attempts → 回到窗口 → 成功后 ack。
    assert local_db.replay_dle_register_dead_letter(eid) is True

    def _ok_post(url, **kwargs):
        posted_id = kwargs["json"]["events"][0]["id"]
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"data": {"event_ids": [posted_id]}}
        return resp

    with patch("backend.agent.aee.device_log_event_client.requests.post", side_effect=_ok_post):
        assert client.drain_register_outbox() == 2
    assert local_db.get_pending_dle_registers() == []
    assert local_db.replay_dle_register_dead_letter(eid) is False  # 已 ack，非死信


def test_legacy_dle_outbox_table_gets_dead_letter_column(tmp_path):
    """#1204：已部署 agent（dle_register_outbox 无 dead_letter 列）初始化后
    幂等 ALTER 补列，enqueue/bump/mark 全链路可用。"""
    import sqlite3

    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE dle_register_outbox ("
        " event_id TEXT PRIMARY KEY, payload TEXT NOT NULL,"
        " created_at TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,"
        " last_error TEXT, acked INTEGER NOT NULL DEFAULT 0)"
    )
    conn.commit()
    conn.close()

    db = LocalDB()
    db.initialize(str(path))
    db.enqueue_dle_register("legacy-evt", {"id": "legacy-evt", "state": "LOCAL"})
    new_attempts = db.bump_dle_register_attempts("legacy-evt", error="HTTP 403")
    assert new_attempts == 1
    db.mark_dle_register_dead_letter("legacy-evt", "HTTP 403")
    assert db.get_pending_dle_registers() == []  # dead 行被过滤
    db.close()
