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
            return "remote_evt\n__STP_RC__:0\n"
        if cmd.startswith("ls -1 /data/vendor/uniview"):
            return "__STP_RC__:2\n"
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


def test_emit_aee_ts_is_device_timestamp_not_subtype(tmp_path):
    """#785: aee_ts 必须是设备时间戳原文，不得塞 event_subtype。"""
    emitter = _RecordingEmitter()
    r = _make_reconciler(tmp_path, emitter=emitter)
    root = tmp_path / "aee_local" / "uniview_watcher" / "0908" / "UNI-1"
    ev = root / "evt_ts"
    ev.mkdir(parents=True)
    (ev / "unievent_info.json").write_text(
        json.dumps({
            "event_name": "KE",
            "package_name": "sys",
            "timestamp": "2026-09-01 12:34:56",
        }),
        encoding="utf-8",
    )
    assert r.tick_once() == 1
    extra = emitter.calls[0]["extra"]
    assert extra["event_subtype"] == "KE"
    assert extra["aee_ts"] == "2026-09-01 12:34:56"
    assert extra["aee_ts"] != extra["event_subtype"]
    # 无时区原文时 to_utc 可为 None（与 MTK 口径一致）


class TestProcessedPrune:
    """#767：_processed 只增不减 → 状态存储线性膨胀。

    裁剪语义：名字「连续 N 拍不在设备列表且不在当前本地树」才移除——
    设备仍存留的事件不会被重拉重发，本地树内事件仍被重扫去重；设备列表
    失败（None）当拍不裁剪且清零滞回。state key/JSON 格式不变，存量全量
    集随列表恢复自然收敛。
    """

    def _seed_store(self, names, serial_key: str = "watcher:unisoc:UNI-1:processed_event_dirs"):
        store = _MemStore()
        store.set_state(serial_key, json.dumps(sorted(names)))
        return store

    def _device_shell(self, names):
        """返回 shell_fn：两个 root 都列出 names（或 None=失败）。"""
        lines = list(names) + ["__STP_RC__:0"]
        listing = "\n".join(lines) + "\n"
        return lambda cmd, _t: (
            listing if cmd.startswith("ls -1 /data/") else None
        )

    def test_stale_name_pruned_after_streak_live_kept(self, tmp_path, monkeypatch):
        monkeypatch.setenv("STP_WATCHER_UNISOC_PROCESSED_PRUNE_AFTER_TICKS", "2")
        store = self._seed_store(["live1", "stale1", "stale2"])
        r = _make_reconciler(
            tmp_path, store=store, shell_fn=self._device_shell(["live1"]),
        )
        r._load_processed_state()
        assert r.tick_once() == 0  # 滞回第 1 拍：不裁剪
        assert r._processed == {"live1", "stale1", "stale2"}
        assert r.tick_once() == 0  # 滞回第 2 拍：stale 裁剪
        assert r._processed == {"live1"}
        assert json.loads(store._data[r._state_key()]) == ["live1"]

    def test_local_tree_name_kept_even_if_absent_from_device(self, tmp_path, monkeypatch):
        monkeypatch.setenv("STP_WATCHER_UNISOC_PROCESSED_PRUNE_AFTER_TICKS", "1")
        store = self._seed_store(["loc1", "gone1"])
        r = _make_reconciler(
            tmp_path, store=store, shell_fn=self._device_shell([]),
        )
        r._load_processed_state()
        # loc1 在当前 stamp 本地树（设备已清理但本地未滚动）：仍须去重
        root = tmp_path / "aee_local" / "uniview_watcher" / "0908" / "UNI-1"
        ev = root / "loc1"
        ev.mkdir(parents=True)
        (ev / "unievent_info.json").write_text("{}", encoding="utf-8")
        r.tick_once()
        assert r._processed == {"loc1"}

    def test_listing_failure_suspends_prune_and_resets_streak(self, tmp_path, monkeypatch):
        monkeypatch.setenv("STP_WATCHER_UNISOC_PROCESSED_PRUNE_AFTER_TICKS", "1")
        store = self._seed_store(["stale1"])
        r = _make_reconciler(
            tmp_path, store=store, shell_fn=lambda *_a, **_k: None,
        )
        r._load_processed_state()
        r.tick_once()
        assert r._processed == {"stale1"}  # 列表失败：不裁剪
        assert r._absent_streak == {}     # 滞回清零

    def test_missing_root_is_authoritative_empty(self, tmp_path, monkeypatch):
        """#1820：两 root 均 rc!=0 是权威空列表——裁剪照常收敛，不再永久挂起。"""
        monkeypatch.setenv("STP_WATCHER_UNISOC_PROCESSED_PRUNE_AFTER_TICKS", "1")
        store = self._seed_store(["stale1", "stale2"])

        def shell_fn(cmd: str, _t: int):
            return (
                "__STP_RC__:2\n" if cmd.startswith("ls -1 /data/") else None
            )

        r = _make_reconciler(tmp_path, store=store, shell_fn=shell_fn)
        r._load_processed_state()
        r.tick_once()
        assert r._processed == set()

    def test_missing_marker_treated_as_incomplete(self, tmp_path, monkeypatch):
        """#1820：rc 标记缺失（异常输出/旧桩）保守视为不完整——不裁剪。"""
        monkeypatch.setenv("STP_WATCHER_UNISOC_PROCESSED_PRUNE_AFTER_TICKS", "1")
        store = self._seed_store(["stale1", "live1"])

        def shell_fn(cmd: str, _t: int):
            # 无 __STP_RC__: 标记——模拟异常输出/旧桩
            if cmd.startswith("ls -1 /data/"):
                return "live1\n"
            return None

        r = _make_reconciler(tmp_path, store=store, shell_fn=shell_fn)
        r._load_processed_state()
        r.tick_once()
        assert r._processed == {"stale1", "live1"}

    def test_legacy_huge_set_converges_to_device_listing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("STP_WATCHER_UNISOC_PROCESSED_PRUNE_AFTER_TICKS", "2")
        legacy = {f"evt_{i:05d}" for i in range(1000)} | {"live_a", "live_b"}
        store = self._seed_store(legacy)
        r = _make_reconciler(
            tmp_path, store=store, shell_fn=self._device_shell(["live_a", "live_b"]),
        )
        r._load_processed_state()
        r.tick_once()
        r.tick_once()
        assert r._processed == {"live_a", "live_b"}
        assert len(json.loads(store._data[r._state_key()])) == 2

    def test_pruned_name_not_repulled(self, tmp_path, monkeypatch):
        monkeypatch.setenv("STP_WATCHER_UNISOC_PROCESSED_PRUNE_AFTER_TICKS", "1")
        store = self._seed_store(["stale1", "fresh1"])
        pulls: List[str] = []

        def pull_fn(remote: str, _local: str, _t: int) -> bool:
            pulls.append(remote)
            return False

        def shell_fn(cmd: str, _t: int):
            if cmd.startswith("ls -1 /data/"):
                return "fresh1\n__STP_RC__:0\n"
            if "unievent_info.json" in cmd:
                return "unievent_info.json\n"
            return None

        r = _make_reconciler(
            tmp_path, store=store, shell_fn=shell_fn, pull_fn=pull_fn,
        )
        r._load_processed_state()
        r.tick_once()  # stale1 裁剪（设备已无）；fresh1 在 processed → 不重拉
        assert not any("stale1" in p for p in pulls)
        assert not any("fresh1" in p for p in pulls)

    def test_hard_cap_evicts_longest_absent_first(self, tmp_path, monkeypatch):
        monkeypatch.setenv("STP_WATCHER_UNISOC_PROCESSED_PRUNE_AFTER_TICKS", "1000")
        monkeypatch.setenv("STP_WATCHER_UNISOC_PROCESSED_MAX_ENTRIES", "5")
        names = {f"evt_{i:02d}" for i in range(10)} | {"live1"}
        store = self._seed_store(names)
        r = _make_reconciler(
            tmp_path, store=store, shell_fn=self._device_shell(["live1"]),
        )
        r._load_processed_state()
        r.tick_once()  # 滞回未到 → 靠硬上限驱逐；live1 滞回 0 最不易被驱逐
        assert len(r._processed) <= 5
        assert "live1" in r._processed

    def test_prune_alone_triggers_state_save(self, tmp_path, monkeypatch):
        monkeypatch.setenv("STP_WATCHER_UNISOC_PROCESSED_PRUNE_AFTER_TICKS", "1")
        store = self._seed_store(["stale1", "live1"])
        r = _make_reconciler(
            tmp_path, store=store, shell_fn=self._device_shell(["live1"]),
        )
        r._load_processed_state()
        assert r.tick_once() == 0  # 无新发射，仅裁剪
        # 裁剪必须落盘（否则重启后旧集回归）
        assert json.loads(store._data[r._state_key()]) == ["live1"]

    def test_missing_uniview_root_treated_as_empty_allows_prune(
        self, tmp_path, monkeypatch,
    ):
        """#1820：单 root 持久缺失（ls rc≠0）不阻塞裁剪。"""
        monkeypatch.setenv("STP_WATCHER_UNISOC_PROCESSED_PRUNE_AFTER_TICKS", "1")
        store = self._seed_store(["stale1", "live1"])

        def shell_fn(cmd: str, _t: int):
            if cmd.startswith("ls -1 /data/uniview"):
                return "live1\n__STP_RC__:0\n"
            if cmd.startswith("ls -1 /data/vendor/uniview"):
                return "__STP_RC__:2\n"
            return None

        r = _make_reconciler(tmp_path, store=store, shell_fn=shell_fn)
        r._load_processed_state()
        r.tick_once()
        assert r._processed == {"live1"}
