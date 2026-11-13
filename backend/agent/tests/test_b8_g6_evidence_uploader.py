"""B8 G6：Agent 代表证据上送器（复制/校验/回执/持久尝试）。"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest

from backend.agent import event_uploader as event_uploader_module
from backend.agent import evidence_uploader as module
from backend.agent.contracts.evidence_layout import dir_sha256_and_size
from backend.agent.evidence_uploader import EvidenceUploader

FOLDER = "MLD-NX3-P535AA-HONOR-NX3-16-1031V78_1031_MonkeyAEEinfo"
EVENT = "2026_1031_120000_123_db.74.ANR"
SERIAL = "AZ6Q123456"


class _Response:
    status_code = 200
    text = ""

    def __init__(self, data):
        self._data = data

    def json(self):
        return {"data": self._data}


@pytest.fixture
def env(tmp_path, monkeypatch):
    local_root = tmp_path / "local"
    log_root = tmp_path / "log"
    local_root.mkdir()
    log_root.mkdir()
    monkeypatch.setenv("STP_AEE_LOCAL_ROOT", str(local_root))
    monkeypatch.setenv("STP_AEE_NFS_ROOT", str(log_root))
    monkeypatch.delenv("STP_AGENT_STATE_DB", raising=False)
    monkeypatch.delenv("STP_DEVICE_LOG_EVENT_ENABLED", raising=False)
    uploader = EvidenceUploader()
    uploader.configure(
        api_url="http://cp", agent_secret="s", host_id="101",
        nfs_root=str(log_root),
    )
    return uploader, local_root, log_root


def _event_dir(local_root: Path) -> Path:
    event = local_root / FOLDER / SERIAL / "aee_exp" / EVENT
    event.mkdir(parents=True)
    (event / "main.dbg").write_bytes(b"dbg")
    return event


def _task(event: Path, *, serial=SERIAL, attempts=0):
    return {
        "publication_id": 7,
        "dle_id": "0f7b9c1e-0000-0000-0000-000000000001",
        "local_path": str(event),
        "destination_rel_path": f"evidence/42_{FOLDER}/{SERIAL}/{EVENT}",
        "root_plan_run_id": 42,
        "serial": serial,
        "event_dir": EVENT,
        "attempts": attempts,
    }


def _capture_posts(monkeypatch):
    calls = []

    def fake_post(url, json, headers, timeout):
        calls.append(json)
        if json["state"] == "VERIFIED":
            return _Response({"state": "VERIFIED"})
        return _Response({"state": json["state"]})

    monkeypatch.setattr(module.requests, "post", fake_post)
    return calls


def test_upload_copies_and_reports_verified_without_pruning_source(env, monkeypatch):
    uploader, local_root, log_root = env
    event = _event_dir(local_root)
    calls = _capture_posts(monkeypatch)
    uploader._upload(_task(event))

    dest = log_root / f"evidence/42_{FOLDER}/{SERIAL}/{EVENT}"
    assert dest.is_dir()
    assert dir_sha256_and_size(dest) == dir_sha256_and_size(event)
    assert event.is_dir()  # I4：中心回执前/后都不删本地源
    assert calls and calls[-1]["state"] == "VERIFIED"
    assert calls[-1]["checksum"] == dir_sha256_and_size(event)[0]


def test_lost_ack_converges_on_existing_identical_destination(env, monkeypatch):
    uploader, local_root, log_root = env
    event = _event_dir(local_root)
    dest = log_root / f"evidence/42_{FOLDER}/{SERIAL}/{EVENT}"
    dest.mkdir(parents=True)
    (dest / "main.dbg").write_bytes(b"dbg")
    calls = _capture_posts(monkeypatch)
    uploader._upload(_task(event))
    assert calls[-1]["state"] == "VERIFIED"
    assert (dest / "main.dbg").read_bytes() == b"dbg"


def test_destination_conflict_refuses_overwrite(env, monkeypatch):
    uploader, local_root, log_root = env
    event = _event_dir(local_root)
    dest = log_root / f"evidence/42_{FOLDER}/{SERIAL}/{EVENT}"
    dest.mkdir(parents=True)
    (dest / "main.dbg").write_bytes(b"other")
    calls = _capture_posts(monkeypatch)
    uploader._upload(_task(event))
    assert calls[-1]["state"] == "FAILED"
    assert calls[-1]["reason"] == "destination_conflict"
    assert (dest / "main.dbg").read_bytes() == b"other"


def test_missing_source_retries_then_fails_at_five(env, monkeypatch):
    uploader, local_root, _log_root = env
    event = local_root / FOLDER / SERIAL / "aee_exp" / EVENT
    calls = _capture_posts(monkeypatch)
    uploader._upload(_task(event, attempts=4))
    assert calls[-1]["state"] == "FAILED"
    assert calls[-1]["attempts"] == 5


def test_wrong_serial_in_task_is_rejected_before_copy(env, monkeypatch):
    uploader, local_root, log_root = env
    event = _event_dir(local_root)
    calls = _capture_posts(monkeypatch)
    uploader._upload(_task(event, serial="OTHER"))
    assert calls[-1]["state"] == "FAILED"
    assert calls[-1]["reason"] == "destination_conflict"
    assert not (log_root / "evidence").exists()


def test_durable_attempts_survive_restart(env, monkeypatch):
    uploader, local_root, _log_root = env
    state = {"7": 4}

    monkeypatch.setattr(module, "_durable_get", lambda pid: state.get(str(pid)))
    monkeypatch.setattr(module, "_durable_set", lambda pid, value: state.__setitem__(str(pid), value))
    event = local_root / FOLDER / SERIAL / "aee_exp" / EVENT
    calls = _capture_posts(monkeypatch)
    uploader.configure(
        api_url="http://cp", agent_secret="s", host_id="101",
        nfs_root=str(_log_root), force=True,
    )
    uploader._upload(_task(event, attempts=0))
    assert calls[-1]["state"] == "FAILED"
    assert calls[-1]["attempts"] == 5



def _configure(uploader, log_root):
    uploader.configure(
        api_url="http://cp", agent_secret="s", host_id="101",
        nfs_root=str(log_root), force=True,
    )
    return uploader


def _state_db(tmp_path, monkeypatch):
    path = tmp_path / "agent_state.db"
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS agent_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setenv("STP_AGENT_STATE_DB", str(path))
    return path


def test_dedup_first_without_spill_intent_is_rejected_before_queue(
    env, monkeypatch,
):
    from backend.agent.event_uploader import EventUploader

    uploader = _configure(EventUploader(), env[2])
    monkeypatch.setattr(event_uploader_module, "_durable_spill_flag", lambda *_a, **_k: False)
    event = {
        "id": str(uuid4()), "local_path": str(env[1] / "event"),
        "plan_run_id": 42, "serial": SERIAL, "platform": "MTK",
        "event_type": "JE", "detected_at": "2026-11-13T00:00:00+00:00",
        "host_id": "101", "dedup_first": True,
    }
    assert uploader.enqueue_local_event(event=event, force=True) is False
    assert uploader._queue.empty()
    assert str(event["id"]) not in uploader._active_ids


def test_dedup_first_spill_intent_is_allowed_and_keeps_attempt_budget(env):
    from backend.agent.event_uploader import EventUploader

    uploader = _configure(EventUploader(), env[2])
    event = {
        "id": str(uuid4()), "local_path": str(env[1] / "event"),
        "plan_run_id": 42, "serial": SERIAL, "platform": "MTK",
        "event_type": "JE", "detected_at": "2026-11-13T00:00:00+00:00",
        "host_id": "101", "dedup_first": True, "hdd_spill": True,
        "upload_attempts": 4,
    }
    assert uploader.enqueue_local_event(event=event, force=True) is True
    job = uploader._queue.get_nowait()
    assert job.attempt == 4
    assert job.hdd_spill is True
    assert job.prune_after_upload is True


def test_durable_spill_intent_survives_restart(tmp_path, monkeypatch):
    from backend.agent.event_uploader import EventUploader

    local_root = tmp_path / "local"
    log_root = tmp_path / "log"
    local_root.mkdir()
    log_root.mkdir()
    monkeypatch.setenv("STP_AEE_LOCAL_ROOT", str(local_root))
    monkeypatch.setenv("STP_AEE_NFS_ROOT", str(log_root))
    monkeypatch.delenv("STP_DEVICE_LOG_EVENT_ENABLED", raising=False)
    _state_db(tmp_path, monkeypatch)
    event_id = str(uuid4())

    first = _configure(EventUploader(), log_root)
    assert first.enqueue_local_event(event={
        "id": event_id, "local_path": str(local_root / "event"),
        "plan_run_id": 42, "serial": SERIAL, "platform": "MTK",
        "event_type": "JE", "detected_at": "2026-11-13T00:00:00+00:00",
        "host_id": "101", "dedup_first": True, "hdd_spill": True,
    }, force=True) is True
    first._queue.get_nowait()  # 首传失败前进程退出；只保留持久意图

    restarted = _configure(EventUploader(), log_root)
    assert restarted.enqueue_local_event(event={
        "id": event_id, "local_path": str(local_root / "event"),
        "plan_run_id": 42, "serial": SERIAL, "platform": "MTK",
        "event_type": "JE", "detected_at": "2026-11-13T00:00:00+00:00",
        "host_id": "101", "dedup_first": True,
    }, force=True) is True
    job = restarted._queue.get_nowait()
    assert job.hdd_spill is True
    assert job.prune_after_upload is True



def test_spill_intent_uses_main_agent_state_db_when_env_unset(tmp_path, monkeypatch):
    from backend.agent import event_uploader as event_uploader_module

    monkeypatch.delenv("STP_AGENT_STATE_DB", raising=False)
    state_db = tmp_path / "agent_state.db"
    conn = sqlite3.connect(state_db)
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS agent_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setattr(event_uploader_module, "BASE_DIR", tmp_path)
    event_id = str(uuid4())
    assert event_uploader_module._durable_spill_flag(event_id, True) is True
    assert event_uploader_module._durable_spill_flag(event_id) is True
    # 未创建库时不得凭空生成文件
    monkeypatch.setattr(event_uploader_module, "BASE_DIR", tmp_path / "missing")
    (tmp_path / "missing").mkdir()
    assert event_uploader_module._durable_spill_flag("other") is False
    assert not (tmp_path / "missing" / "agent_state.db").exists()


def test_durable_spill_event_ids_are_bounded_and_skip_cleared(tmp_path, monkeypatch):
    _state_db(tmp_path, monkeypatch)
    ids = [str(uuid4()) for _ in range(3)]
    for event_id in ids:
        assert event_uploader_module._durable_spill_flag(event_id, True)
    cleared = str(uuid4())
    assert event_uploader_module._durable_spill_flag(cleared, True)
    assert event_uploader_module._durable_spill_flag(cleared, False) is False

    assert set(event_uploader_module._durable_spill_event_ids()) == set(ids)
    assert len(event_uploader_module._durable_spill_event_ids(limit=2)) == 2


def test_spill_intent_recovery_by_id_respects_budget_and_identity(
    tmp_path, monkeypatch,
):
    """按 ID 恢复：已耗尽预算的事件不从零开始，普通事件不受影响。"""
    from backend.agent.event_uploader import EventUploader

    _state_db(tmp_path, monkeypatch)
    up = EventUploader()
    up.configure(
        api_url="http://cp", agent_secret="s", host_id="101",
        nfs_root=str(tmp_path / "nfs"),
    )
    up._stop_evt.clear()

    exhausted = str(uuid4())
    resumable = str(uuid4())
    assert event_uploader_module._durable_spill_flag(exhausted, True)
    assert event_uploader_module._durable_spill_flag(resumable, True)
    event_uploader_module._durable_set_attempts(exhausted, event_uploader_module._MAX_RETRIES)
    event_uploader_module._durable_set_attempts(resumable, 2)

    requested = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        requested.update(params)
        return _Response({"events": [
            {
                "id": exhausted, "state": "UPLOAD_FAILED",
                "local_path": "/data/aee/exhausted", "serial": SERIAL,
                "platform": "MTK", "event_type": "JE",
                "detected_at": "2026-11-01T00:00:00+00:00",
                "plan_run_id": 42, "host_id": "101", "dedup_first": True,
                "hdd_spill": False,
            },
            {
                "id": resumable, "state": "UPLOAD_FAILED",
                "local_path": "/data/aee/resumable", "serial": SERIAL,
                "platform": "MTK", "event_type": "JE",
                "detected_at": "2026-11-01T00:00:01+00:00",
                "plan_run_id": 42, "host_id": "101", "dedup_first": True,
                "hdd_spill": False,
            },
        ]})

    monkeypatch.setattr(event_uploader_module.requests, "get", fake_get)
    up._recover_spill_intents("UPLOAD_FAILED,UPLOADING")

    # 按事件 ID 精确取回，且不带 purpose（无台账的首次失败必须可见）
    assert set(requested["ids"].split(",")) == {exhausted, resumable}
    assert "purpose" not in requested
    jobs = []
    while not up._queue.empty():
        jobs.append(up._queue.get_nowait())
    assert [job.event_id for job in jobs] == [resumable]
    assert jobs[0].attempt == 2
    assert jobs[0].hdd_spill is True
    assert up._load_attempts(exhausted) == event_uploader_module._MAX_RETRIES
