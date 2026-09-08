"""#1043 — UNISOC realtime chain: producer + UNIVIEW emit + state API."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.agent.aee.collectors.unisoc import UnisocPlatformCollector
from backend.agent.aee.unisoc_reconciler import UnisocUniviewReconciler
from backend.agent.registry.local_db import LocalDB
from backend.agent.watcher.emitter import SignalEmitter


class _RecordingEmitter:
    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []
        self._seq = 0

    def emit(self, **kwargs) -> int:
        self._seq += 1
        self.calls.append(dict(kwargs))
        return self._seq


class _MemStore:
    def __init__(self) -> None:
        self._data: Dict[str, str] = {}

    def get_state(self, key: str, default: str = "") -> str:
        return self._data.get(key, default)

    def set_state(self, key: str, value: str) -> None:
        self._data[key] = value


class _FakeDleClient:
    def __init__(self) -> None:
        self.created: List[Dict[str, Any]] = []

    def create_local_event(self, **kwargs) -> None:
        self.created.append(dict(kwargs))

    @staticmethod
    def dir_size_bytes(path: Path) -> int:
        total = 0
        for p in path.rglob("*"):
            if p.is_file():
                total += p.stat().st_size
        return total


def _make_reconciler(
    tmp_path: Path,
    *,
    emitter=None,
    store=None,
    shell_fn=None,
    pull_fn=None,
    device_log_client=None,
) -> UnisocUniviewReconciler:
    return UnisocUniviewReconciler(
        signal_emitter=emitter or _RecordingEmitter(),
        state_store=store if store is not None else _MemStore(),
        serial="UNI-1",
        job_id=42,
        host_id="host-u",
        local_root=tmp_path / "aee_local",
        run_date_stamp="0908",
        baseline_interval_seconds=3600,
        platform_collector=UnisocPlatformCollector(),
        device_log_client=device_log_client,
        shell_fn=shell_fn or (lambda *_a, **_k: None),
        pull_fn=pull_fn or (lambda *_a, **_k: False),
        plan_run_id=7,
    )


def test_tick_once_emits_uniview_and_creates_dle(tmp_path):
    """Pre-seeded local event → real collector + emit UNIVIEW + DLE."""
    emitter = _RecordingEmitter()
    dle = _FakeDleClient()
    r = _make_reconciler(tmp_path, emitter=emitter, device_log_client=dle)
    root = tmp_path / "aee_local" / "uniview_watcher" / "0908" / "UNI-1"
    ev = root / "evt_ke_1"
    ev.mkdir(parents=True)
    (ev / "unievent_info.json").write_text(
        json.dumps({"event_name": "KE", "package_name": "sys"}),
        encoding="utf-8",
    )

    assert r.tick_once() == 1
    assert len(emitter.calls) == 1
    assert emitter.calls[0]["category"] == "UNIVIEW"
    assert emitter.calls[0]["source"] == "reconciler"
    assert len(dle.created) == 1
    assert dle.created[0]["event_type"] == "UNIVIEW"
    assert dle.created[0]["event_subtype"] == "KE"

    # Idempotent: processed state persists via get_state/set_state
    assert r.tick_once() == 0


def test_tick_once_pulls_device_events_then_emits(tmp_path):
    """Fake adb listing + pull populates local tree then emits (#1043 producer)."""
    device_root = tmp_path / "device" / "uniview"
    remote_ev = device_root / "remote_evt"
    remote_ev.mkdir(parents=True)
    (remote_ev / "unievent_info.json").write_text(
        json.dumps({"event_name": "NE", "package": "app"}),
        encoding="utf-8",
    )

    def shell_fn(cmd: str, _timeout: int) -> Optional[str]:
        if cmd.startswith("ls -1 /data/uniview"):
            return "remote_evt\n"
        if "unievent_info.json" in cmd and "remote_evt" in cmd:
            return "unievent_info.json\n"
        return None

    def pull_fn(remote: str, local: str, _timeout: int) -> bool:
        assert remote.endswith("/remote_evt")
        dest = Path(local) / "remote_evt"
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copytree(remote_ev, dest, dirs_exist_ok=True)
        return True

    emitter = _RecordingEmitter()
    r = _make_reconciler(
        tmp_path, emitter=emitter, shell_fn=shell_fn, pull_fn=pull_fn,
    )
    assert r.tick_once() == 1
    local = (
        tmp_path / "aee_local" / "uniview_watcher" / "0908" / "UNI-1" / "remote_evt"
        / "unievent_info.json"
    )
    assert local.is_file()
    assert emitter.calls[0]["category"] == "UNIVIEW"
    assert emitter.calls[0]["extra"]["event_subtype"] == "NE"


def test_real_signal_emitter_accepts_uniview_category(tmp_path):
    """Contract + SignalEmitter path accepts category=UNIVIEW (#1043 / #806)."""
    db = LocalDB()
    db.initialize(str(tmp_path / "agent.db"))
    try:
        emitter = SignalEmitter(
            local_db=db,
            job_id=9,
            host_id="h",
            device_serial="S",
            fencing_token="9:1",
            agent_instance_id="agent-test",
        )
        seq = emitter.emit(
            category="UNIVIEW",
            source="reconciler",
            path_on_device="evt1",
            artifact_uri="/tmp/evt1",
            extra={"event_type": "UNIVIEW"},
        )
        assert seq == 1
        rows = db.get_pending_log_signals()
        assert rows[0]["envelope"]["category"] == "UNIVIEW"
    finally:
        db.close()


def test_processed_state_uses_get_set_state(tmp_path):
    store = _MemStore()
    emitter = _RecordingEmitter()
    r = _make_reconciler(tmp_path, emitter=emitter, store=store)
    root = tmp_path / "aee_local" / "uniview_watcher" / "0908" / "UNI-1"
    ev = root / "e1"
    ev.mkdir(parents=True)
    (ev / "unievent_info.json").write_text(
        json.dumps({"event_name": "ANR"}), encoding="utf-8",
    )
    assert r.tick_once() == 1
    key = r._state_key()
    assert key in store._data
    assert "e1" in json.loads(store._data[key])

    r2 = _make_reconciler(tmp_path, emitter=_RecordingEmitter(), store=store)
    r2._load_processed_state()
    assert "e1" in r2._processed
