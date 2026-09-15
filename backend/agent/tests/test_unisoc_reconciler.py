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

def _ls_l(names, *, sig="drwxrwxrwx 2 root root 3452 2026-09-14 22:49"):
    """#2010：把名字列表渲染成 `ls -l` 形状（size+mtime 即签名），供 shell_fn 桩使用。"""
    listing = "".join(f"{sig} {n}\n" for n in names)
    return listing + "__STP_RC__:0\n"


def _real_unievent_info(*, event_id: str, event_name: str, proc: str, kick: str, tag: str) -> str:
    """#2083：真机形状夹具——A 设备头 + B 元数据行 + C 发生行（JSONL）。"""
    return "\n".join([
        json.dumps({"sn": "UNI-1", "software_version": "MyOS16.0.1_Z2581_GEN_AF",
                    "soc_model": "UMS9230E", "event_count": "1"}, ensure_ascii=False),
        json.dumps({"event_id": event_id, "event_type": "FAULT",
                    "event_level": "GENERAL", "event_name": event_name},
                   ensure_ascii=False),
        json.dumps({"kick_datetime": kick, "pid": "1234", "proc": proc, "tag": tag},
                   ensure_ascii=False),
    ])




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
    collector=None,
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
        platform_collector=collector or UnisocPlatformCollector(),
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
    (ev / "unievent_info").write_text(
        _real_unievent_info(event_id="103000099", event_name="KE",
                            proc="sys", kick="2026-09-08_06:59:12.031",
                            tag="system_app_crash"),
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


def test_same_dir_with_new_content_is_repulled_and_reemitted(tmp_path):
    """#2010：展锐复用同一 event_id 目录追加新异常（真机已确认）。

    目录名不变、内容签名（size+mtime）变化 → 必须重拉并**再次发射**；
    签名不变 → 不得重发（本用例第 2 拍即反例）。
    """
    emitter = _RecordingEmitter()
    root = tmp_path / "aee_local" / "uniview_watcher" / "0908" / "UNI-1"
    ev = root / "JE.103000004"
    ev.mkdir(parents=True)
    (ev / "unievent_info").write_text(
        _real_unievent_info(event_id="103000004", event_name="Java Crash",
                            proc="com.android.camera2",
                            kick="2026-09-08_06:59:12.031",
                            tag="system_app_crash"),
        encoding="utf-8",
    )
    sig = {"v": "drwxrwxrwx 2 root root 3452 2026-09-08 06:59"}

    def shell_fn(cmd: str, _t: int):
        if cmd.startswith("ls -l /data/ylog/uniview_exception"):
            return f"{sig['v']} JE.103000004\n__STP_RC__:0\n"
        if cmd.startswith("ls -l /data/"):
            return "__STP_RC__:2\n"
        if "unievent_info" in cmd:
            return "unievent_info\n"
        return None

    pulls: List[str] = []

    def pull_fn(remote: str, _local: str, _t: int) -> bool:
        pulls.append(remote)
        return True

    r = _make_reconciler(tmp_path, emitter=emitter, shell_fn=shell_fn, pull_fn=pull_fn)
    assert r.tick_once() == 1   # 首拍：从未处理 → 发射
    first_pulls = len(pulls)
    assert r.tick_once() == 0   # 签名未变 → 不重拉、不重发（反例）
    assert len(pulls) == first_pulls, "签名未变却重拉"
    sig["v"] = "drwxrwxrwx 2 root root 3452 2026-09-14 22:49"   # 目录被追加新异常
    assert r.tick_once() == 1   # 签名变化 → 重拉 + 重发
    assert len(pulls) == first_pulls + 1, "签名变化却没有重拉"
    assert len(emitter.calls) == 2


def test_tick_once_pulls_device_events_then_emits(tmp_path):
    """Fake adb listing + pull populates local tree then emits (#1043 producer)."""
    device_root = tmp_path / "device" / "uniview"
    remote_ev = device_root / "remote_evt"
    remote_ev.mkdir(parents=True)
    (remote_ev / "unievent_info").write_text(
        _real_unievent_info(event_id="103000003", event_name="NE",
                            proc="app", kick="2026-09-07_01:36:44.329",
                            tag="native_crash"),
        encoding="utf-8",
    )

    def shell_fn(cmd: str, _timeout: int) -> Optional[str]:
        if cmd.startswith("ls -l /data/ylog/uniview_exception"):
            return _ls_l(["remote_evt"])
        if cmd.startswith("ls -l /data/vendor/uniview"):
            return "__STP_RC__:2\n"
        if "unievent_info" in cmd and "remote_evt" in cmd:
            return "unievent_info\n"
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
        / "unievent_info"
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
    (ev / "unievent_info").write_text(
        _real_unievent_info(event_id="103000005", event_name="ANR",
                            proc="com.android.nfc",
                            kick="2026-08-15_09:31:40.477",
                            tag="system_app_anr"),
        encoding="utf-8",
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
    (ev / "unievent_info").write_text(
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
        listing = _ls_l(names)
        return lambda cmd, _t: (
            listing if cmd.startswith("ls -l /data/") else None
        )

    def test_stale_name_pruned_after_streak_live_kept(self, tmp_path, monkeypatch):
        monkeypatch.setenv("STP_WATCHER_UNISOC_PROCESSED_PRUNE_AFTER_TICKS", "2")
        store = self._seed_store(["live1", "stale1", "stale2"])
        r = _make_reconciler(
            tmp_path, store=store, shell_fn=self._device_shell(["live1"]),
        )
        r._load_processed_state()
        assert r.tick_once() == 0  # 滞回第 1 拍：不裁剪
        assert set(r._processed) == {"live1", "stale1", "stale2"}
        assert r.tick_once() == 0  # 滞回第 2 拍：stale 裁剪
        assert set(r._processed) == {"live1"}
        assert list(json.loads(store._data[r._state_key()])) == ["live1"]

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
        (ev / "unievent_info").write_text("{}", encoding="utf-8")
        r.tick_once()
        assert set(r._processed) == {"loc1"}

    def test_cap_eviction_never_drops_names_still_present(self, tmp_path, monkeypatch):
        """#2060：上限驱逐不得把仍在场（设备列表/本地树）的条目踢出去。

        原实现只按滞回计数排序取前 overflow 个，未排除在场名字 → 设备事件目录数
        超过上限时，在场条目被驱逐，而 emit 循环唯一的去重就是本集合成员判定
        （`_sync_device_events_to_local` 对已同步目录不再重拉、也不把名字加回来）
        → 稳态下每拍以新 seq_no 重复 emit。
        """
        monkeypatch.setenv("STP_WATCHER_UNISOC_PROCESSED_PRUNE_AFTER_TICKS", "99")
        monkeypatch.setenv("STP_WATCHER_UNISOC_PROCESSED_MAX_ENTRIES", "2")
        store = self._seed_store(["live1", "live2", "live3"])
        r = _make_reconciler(
            tmp_path, store=store,
            shell_fn=self._device_shell(["live1", "live2", "live3"]),
        )
        r._load_processed_state()
        r.tick_once()
        assert set(r._processed) == {"live1", "live2", "live3"}, (
            "在场条目被上限驱逐 → 下一拍会以新 seq_no 重发（稳态重复 emit）"
        )

    def test_cap_eviction_still_drops_absent_entries(self, tmp_path, monkeypatch):
        """上限仍是防膨胀的最后防线：不在场的条目照常驱逐（行为不变）。"""
        monkeypatch.setenv("STP_WATCHER_UNISOC_PROCESSED_PRUNE_AFTER_TICKS", "99")
        monkeypatch.setenv("STP_WATCHER_UNISOC_PROCESSED_MAX_ENTRIES", "2")
        store = self._seed_store(["live1", "gone1", "gone2"])
        r = _make_reconciler(
            tmp_path, store=store, shell_fn=self._device_shell(["live1"]),
        )
        r._load_processed_state()
        r.tick_once()
        # 上限只要求「不超过」：本次 overflow=1，故驱逐 1 条不在场的、保留在场的那条
        assert "live1" in r._processed, "在场条目必须保留"
        assert len(r._processed) == 2
        assert len({"gone1", "gone2"} & set(r._processed)) == 1

    def test_listing_failure_suspends_prune_and_resets_streak(self, tmp_path, monkeypatch):
        monkeypatch.setenv("STP_WATCHER_UNISOC_PROCESSED_PRUNE_AFTER_TICKS", "1")
        store = self._seed_store(["stale1"])
        r = _make_reconciler(
            tmp_path, store=store, shell_fn=lambda *_a, **_k: None,
        )
        r._load_processed_state()
        r.tick_once()
        assert set(r._processed) == {"stale1"}  # 列表失败：不裁剪
        assert r._absent_streak == {}     # 滞回清零

    def test_missing_root_is_authoritative_empty(self, tmp_path, monkeypatch):
        """#1820：两 root 均 rc!=0 是权威空列表——裁剪照常收敛，不再永久挂起。"""
        monkeypatch.setenv("STP_WATCHER_UNISOC_PROCESSED_PRUNE_AFTER_TICKS", "1")
        store = self._seed_store(["stale1", "stale2"])

        def shell_fn(cmd: str, _t: int):
            return (
                "__STP_RC__:2\n" if cmd.startswith("ls -l /data/") else None
            )

        r = _make_reconciler(tmp_path, store=store, shell_fn=shell_fn)
        r._load_processed_state()
        r.tick_once()
        assert set(r._processed) == set()

    def test_missing_marker_treated_as_incomplete(self, tmp_path, monkeypatch):
        """#1820：rc 标记缺失（异常输出/旧桩）保守视为不完整——不裁剪。"""
        monkeypatch.setenv("STP_WATCHER_UNISOC_PROCESSED_PRUNE_AFTER_TICKS", "1")
        store = self._seed_store(["stale1", "live1"])

        def shell_fn(cmd: str, _t: int):
            # 无 __STP_RC__: 标记——模拟异常输出/旧桩
            if cmd.startswith("ls -l /data/"):
                return "live1\n"
            return None

        r = _make_reconciler(tmp_path, store=store, shell_fn=shell_fn)
        r._load_processed_state()
        r.tick_once()
        assert set(r._processed) == {"stale1", "live1"}

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
        assert set(r._processed) == {"live_a", "live_b"}
        assert len(list(json.loads(store._data[r._state_key()]))) == 2

    def test_pruned_name_not_repulled(self, tmp_path, monkeypatch):
        monkeypatch.setenv("STP_WATCHER_UNISOC_PROCESSED_PRUNE_AFTER_TICKS", "1")
        store = self._seed_store(["stale1", "fresh1"])
        pulls: List[str] = []

        def pull_fn(remote: str, _local: str, _t: int) -> bool:
            pulls.append(remote)
            return False

        def shell_fn(cmd: str, _t: int):
            if cmd.startswith("ls -l /data/"):
                return _ls_l(["fresh1"])
            if "unievent_info" in cmd:
                return "unievent_info\n"
            return None

        r = _make_reconciler(
            tmp_path, store=store, shell_fn=shell_fn, pull_fn=pull_fn,
        )
        r._load_processed_state()
        r.tick_once()
        assert not any("stale1" in p for p in pulls)
        # #2010 行为变化：旧格式状态（签名未知）下本拍会为 fresh1 重新确认一次内容，
        # 以发现「同名目录被追加新异常」。签名落定后不再无谓重拉——
        # 见 test_same_dir_with_new_content_is_repulled_and_reemitted 的第 2 拍。
        assert any("fresh1" in p for p in pulls)

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
        assert list(json.loads(store._data[r._state_key()])) == ["live1"]

    def test_missing_uniview_root_treated_as_empty_allows_prune(
        self, tmp_path, monkeypatch,
    ):
        """#1820：单 root 持久缺失（ls rc≠0）不阻塞裁剪。"""
        monkeypatch.setenv("STP_WATCHER_UNISOC_PROCESSED_PRUNE_AFTER_TICKS", "1")
        store = self._seed_store(["stale1", "live1"])

        def shell_fn(cmd: str, _t: int):
            if cmd.startswith("ls -l /data/ylog/uniview_exception"):
                return _ls_l(["live1"])
            if cmd.startswith("ls -l /data/vendor/uniview"):
                return "__STP_RC__:2\n"
            return None

        r = _make_reconciler(tmp_path, store=store, shell_fn=shell_fn)
        r._load_processed_state()
        r.tick_once()
        assert set(r._processed) == {"live1"}


def _single_dir_device(tmp_path: Path, dirname: str, content: str):
    """单目录设备桩：shell 列举 + pull 拷贝；返回 (shell_fn, pull_fn, pulls)。"""
    remote_ev = tmp_path / "device" / "uniview" / dirname
    remote_ev.mkdir(parents=True)
    (remote_ev / "unievent_info").write_text(content, encoding="utf-8")
    sig = "drwxrwxrwx 2 root root 3452 2026-08-12 02:00"

    def shell_fn(cmd: str, _t: int):
        if cmd.startswith("ls -l /data/ylog/uniview_exception"):
            return f"{sig} {dirname}\n__STP_RC__:0\n"
        if cmd.startswith("ls -l /data/"):
            return "__STP_RC__:2\n"
        if "unievent_info" in cmd:
            return "unievent_info\n"
        return None

    pulls: List[str] = []

    def pull_fn(remote: str, local: str, _t: int) -> bool:
        pulls.append(remote)
        dest = Path(local) / dirname
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copytree(remote_ev, dest, dirs_exist_ok=True)
        return True

    return shell_fn, pull_fn, pulls


def test_normalboot_only_dir_records_signature_and_stops_repull(tmp_path):
    """#2083：确定性不可上报（normalboot-only）也必须落签名，否则每拍重拉。

    真机形状：目录有独立 meta 行（`event_name:"Boot Category"`）、发生行全部
    normalboot。修复前它被 emit 成假阳性；只改判据不落签名，则同步侧短路
    （`prev == signature`）永不成立 → 每拍 `adb pull`（本用例第 2 拍即反例）。
    """
    emitter = _RecordingEmitter()
    store = _MemStore()
    content = "\n".join([
        json.dumps({"sn": "UNI-1", "soc_model": "UMS9230E"}, ensure_ascii=False),
        json.dumps({"event_id": "103000002", "event_type": "FAULT",
                    "event_level": "GENERAL", "event_name": "Boot Category"},
                   ensure_ascii=False),
        json.dumps({"kick_datetime": "2026-08-11_19:50:25.382",
                    "event_time": 1786470625382,
                    "reboot_reason": "normalboot"}, ensure_ascii=False),
    ])
    shell_fn, pull_fn, pulls = _single_dir_device(tmp_path, "Reboot.103000002", content)
    r = _make_reconciler(tmp_path, emitter=emitter, store=store,
                         shell_fn=shell_fn, pull_fn=pull_fn)

    assert r.tick_once() == 0, "normalboot-only 不得 emit（#2083 假阳性）"
    assert emitter.calls == []
    assert len(pulls) == 1
    assert "Reboot.103000002" in r._processed, "确定性不可上报必须落签名"
    assert list(json.loads(store._data[r._state_key()])) == ["Reboot.103000002"]

    assert r.tick_once() == 0
    assert len(pulls) == 1, "签名未变却重拉（#2083 复发）"


class _FlakyCollector:
    """#2083：第 1 次 parse 抛瞬时异常，之后回落真实 collector（模拟文件竞态）。"""

    def __init__(self) -> None:
        self.calls = 0
        self._real = UnisocPlatformCollector()

    def parse_metadata(self, event_dir: Path):
        self.calls += 1
        if self.calls == 1:
            raise OSError("transient read race")
        return self._real.parse_metadata(event_dir)


def test_transient_metadata_error_not_recorded_retries(tmp_path):
    """#2083：非 ``CollectorError`` 的 parse 异常是瞬时失败——不落签名、下一拍重试。"""
    emitter = _RecordingEmitter()
    store = _MemStore()
    content = _real_unievent_info(event_id="103000004", event_name="Java Crash",
                                  proc="com.android.camera2",
                                  kick="2026-09-14_22:48:51.563",
                                  tag="system_app_crash")
    shell_fn, pull_fn, pulls = _single_dir_device(tmp_path, "JE.103000004", content)
    collector = _FlakyCollector()
    r = _make_reconciler(tmp_path, emitter=emitter, store=store, collector=collector,
                         shell_fn=shell_fn, pull_fn=pull_fn)

    assert r.tick_once() == 0
    assert "JE.103000004" not in r._processed, "瞬时失败不得落签名（否则丢事件）"
    assert store._data.get(r._state_key()) is None, "瞬时失败不得写状态"

    assert r.tick_once() == 1, "下一拍必须重试并恢复发射"
    assert len(pulls) == 2
    assert emitter.calls[0]["extra"]["event_subtype"] == "Java Crash"
