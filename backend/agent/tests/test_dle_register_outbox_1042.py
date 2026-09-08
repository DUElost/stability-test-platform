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
