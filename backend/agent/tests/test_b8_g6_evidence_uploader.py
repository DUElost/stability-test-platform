"""B8 G6：Agent 代表证据上送器（复制/校验/回执/持久尝试）。"""

from __future__ import annotations

from pathlib import Path

import pytest

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
