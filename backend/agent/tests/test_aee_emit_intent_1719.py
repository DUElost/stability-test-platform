# -*- coding: utf-8 -*-
"""#1719 AEE emit 补偿通道：意图簿 + 幂等重放的行为测试。

覆盖：
- processor 在 processed 落盘**之前**调用 on_entry_intent（占位语义）；
- reconciler 命中占位 → 先持久化 keys（seq_no + envelope + 事件 UUID）再产生
  效果（outbox enqueue + DLE POST）；done 后幂等重入不再重复；
- sweep 三态：!done 重放 / done+processed 清理 / done+未 processed 保留；
- #803 折衷版（#1687）遗留窗口：processed 已写、emit 未发生 → sweep 补偿；
- 重放复用同一幂等键（seq_no / DLE id），不重新分配；
- 重放失败有界重试（attempts 上限退场并计 signals_dropped）；
- emitter prepare/enqueue 拆分与 LocalDB 幂等；DLE client payload 组装 +
  失败入 outbox 幂等重放；
- #2044：占位写失败**不 finalize**（不推进 processed / 不 emit / 留 pending 重试，
  用尽后带真因显式告警）；
- #2034：跨前缀 done 墓碑回查在**生产路径**上可达（占位先落同前缀簿），且 sweep
  不会在 runtime finalize 前抹掉 baseline 墓碑。
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
from unittest.mock import MagicMock, patch

from backend.agent.aee import emit_intent as ei
from backend.agent.aee.db_history import save_processed_lines, state_key
from backend.agent.aee.device_log_event_client import DeviceLogEventClient
from backend.agent.aee.reconciler import AeeDbHistoryReconciler
from backend.agent.registry.local_db import LocalDB
from backend.agent.watcher.emitter import SignalEmitter


# ----------------------------------------------------------------------
# 替身
# ----------------------------------------------------------------------

class _MemStore:
    """ScriptStateStore 替身（in-memory）+ 写顺序记录。"""

    def __init__(self, order: Optional[List[tuple]] = None) -> None:
        self._data: Dict[str, str] = {}
        self.order = order

    def get_state(self, key: str, default: str = "") -> str:
        return self._data.get(key, default)

    def set_state(self, key: str, value: str) -> None:
        self._data[key] = value
        if self.order is not None:
            self.order.append(("set", key))


class _FakeEmitter:
    """SignalEmitter 替身：prepare 分配、enqueue 记录；可注入失败。"""

    def __init__(
        self,
        *,
        order: Optional[List[tuple]] = None,
        store: Optional[_MemStore] = None,
        fail_prepare: bool = False,
        fail_enqueue: bool = False,
    ) -> None:
        self.prepared: List[Dict[str, Any]] = []
        self.enqueued: List[tuple] = []
        self.enqueue_snapshots: List[str] = []
        self.order = order
        self.store = store
        self.fail_prepare = fail_prepare
        self.fail_enqueue = fail_enqueue
        self._seq = 0
        self._lock = threading.Lock()

    def prepare(self, **kwargs):
        if self.fail_prepare:
            raise RuntimeError("prepare boom")
        with self._lock:
            self._seq += 1
            seq = self._seq
        self.prepared.append(dict(kwargs))
        if self.order is not None:
            self.order.append(("prepare", seq))
        envelope = {"seq_no": seq, "job_id": 901, **kwargs}
        if isinstance(envelope.get("detected_at"), datetime):
            envelope["detected_at"] = envelope["detected_at"].isoformat()
        return seq, envelope

    def enqueue(self, seq_no, envelope):
        if self.fail_enqueue:
            raise RuntimeError("enqueue boom")
        self.enqueued.append((int(seq_no), envelope))
        if self.order is not None:
            self.order.append(("enqueue", int(seq_no)))
        if self.store is not None:
            # 记录效果发生时意图簿的快照——验证 keys 先于效果持久化
            self.enqueue_snapshots.append(
                self.store.get_state(ei.intent_state_key(self._processed_key()), "{}")
            )
        return 1

    def _processed_key(self) -> str:  # 由测试在构造后注入
        return getattr(self, "processed_key", "")

    def emit(self, **kwargs) -> int:
        seq_no, envelope = self.prepare(**kwargs)
        self.enqueue(seq_no, envelope)
        return seq_no


class _FakeDeviceLogClient:
    """DLE 替身：只组装 payload 与记录 POST，不触网。"""

    def __init__(self, *, order: Optional[List[tuple]] = None) -> None:
        self.posts: List[Dict[str, Any]] = []
        self.order = order

    def build_local_event_payload(self, **kwargs) -> Dict[str, Any]:
        return {
            "id": kwargs["event_id"],
            "state": "LOCAL",
            "serial": kwargs["serial"],
            "event_type": kwargs["event_type"],
            "link_signal_seq_no": kwargs["link_signal_seq_no"],
            "local_path": str(kwargs["local_path"]),
            "job_id": kwargs["job_id"],
        }

    def build_pull_failed_payload(self, **kwargs) -> Dict[str, Any]:
        return {
            "id": kwargs["event_id"],
            "state": "PULL_FAILED",
            "serial": kwargs["serial"],
            "event_type": kwargs["event_type"],
            "link_signal_seq_no": kwargs["link_signal_seq_no"],
            "job_id": kwargs["job_id"],
        }

    def post_event_payload(self, payload) -> Optional[str]:
        self.posts.append(dict(payload))
        if self.order is not None:
            self.order.append(("dle_post", payload.get("id")))
        return str(payload.get("id"))

    @staticmethod
    def dir_size_bytes(path) -> int:
        return 42


# ----------------------------------------------------------------------
# 夹具
# ----------------------------------------------------------------------

_JOB_ID = 901
_SERIAL = "SX"
_LINE = "/data/aee_exp/db.01,Java (JE),pkg,_,_,_,_,_,com.example,2026-05-28 10:00:00.000"


def _payload(subdir: Path, *, line: str = _LINE, origin: str = "runtime") -> Dict[str, Any]:
    return {
        "line": line,
        "parsed": {
            "db_path": "/data/aee_exp/db.01",
            "pkg_name": "com.example",
            "timestamp": "2026-05-28 10:00:00.000",
            "event_type": "CRASH",
            "raw_event_type": "Java (JE)",
            "event_subtype": "JE",
        },
        "aee_type": "aee_exp",
        "output_subdir": subdir,
        "entry_origin": origin,
        "state_key_prefix": "watcher:aee",
    }


def _make_reconciler(
    tmp_path: Path,
    *,
    emitter: Optional[_FakeEmitter] = None,
    store: Optional[_MemStore] = None,
    client: Optional[_FakeDeviceLogClient] = None,
) -> AeeDbHistoryReconciler:
    return AeeDbHistoryReconciler(
        signal_emitter=emitter or _FakeEmitter(),
        state_store=store or _MemStore(),
        serial=_SERIAL,
        job_id=_JOB_ID,
        host_id="HOST",
        local_root=tmp_path,
        baseline_snapshot_enabled=False,
        device_log_client=client,
    )


def _processed_key(aee_type: str = "aee_exp", prefix: str = "watcher:aee") -> str:
    return state_key(_SERIAL, aee_type, prefix=prefix)


def _intents(store: _MemStore, prefix: str = "watcher:aee") -> Dict[str, Any]:
    return json.loads(
        store.get_state(ei.intent_state_key(_processed_key(prefix=prefix)), "{}")
    )


# ----------------------------------------------------------------------
# processor：占位先于 processed 落盘
# ----------------------------------------------------------------------

def _setup_pdl_stubs(monkeypatch, history_line: str):
    def shell_fn(cmd: str, timeout: int):
        if "getprop" in cmd:
            props = {
                "ro.product.name": "X6851-OP",
                "ro.build.display.id": "X6851-OP-16.3.0.022(SU_0401)",
                "ro.build.version.incremental": "0401",
                "ro.build.version.release": "16",
            }
            for key, val in props.items():
                if key in cmd:
                    return val
        if "cat /data/aee_exp/db_history" in cmd:
            return history_line + "\n"
        return ""

    def pull_fn(remote: str, local: str, timeout: int) -> bool:
        Path(local).mkdir(parents=True, exist_ok=True)
        (Path(local) / "main.dbg").write_text("ok", encoding="utf-8")
        return True

    from backend.agent.aee import processor as proc_mod

    monkeypatch.setattr(proc_mod, "make_adb_shell_fn", lambda serial, adb_path: lambda cmd, t: shell_fn(cmd, t))
    monkeypatch.setattr(proc_mod, "make_adb_pull_fn", lambda serial, adb_path: pull_fn)
    monkeypatch.setattr(proc_mod, "export_correlated_mobilelogs", lambda **kw: {"matched": 0, "pulled": 0})
    monkeypatch.setattr(proc_mod, "export_bugreport_for_timestamp", lambda **kw: True)


def test_processor_intent_hook_fires_before_processed_save(tmp_path, monkeypatch):
    """on_entry_intent 必须在 processed 落盘之前触发（占位语义）。"""
    monkeypatch.setenv("STP_AEE_LOCAL_ROOT", str(tmp_path))
    store = _MemStore()
    line = "/data/aee_exp/db.77,Java (JE),pkg,_,_,_,_,_,com.example.app,2026-05-28 10:15:22.123"
    _setup_pdl_stubs(monkeypatch, line)

    observed: Dict[str, Any] = {}

    def on_intent(payload: dict) -> None:
        observed["payload"] = dict(payload)
        observed["processed_at_hook"] = store.get_state(
            state_key("dev_intent", "aee_exp"), "[]"
        )

    from backend.agent.aee.processor import ProcessConfig, process_device_logs

    cfg = ProcessConfig(export_mobilelog=False, export_bugreport=False)
    r = process_device_logs(
        serial="dev_intent", job_id=77, state_store=store, config=cfg,
        on_entry_intent=on_intent,
    )

    assert r.pulled == 1
    assert observed["payload"]["line"] == line
    assert observed["payload"]["state_key_prefix"] == "watcher:aee"  # processor 透传
    assert line not in observed["processed_at_hook"]  # 钩子时 processed 尚未落入


# ----------------------------------------------------------------------
# reconciler：占位 → 补 keys → 效果 → done
# ----------------------------------------------------------------------

def test_new_entry_consumes_placeholder_with_keys_before_effects(tmp_path):
    order: List[tuple] = []
    store = _MemStore(order)
    emitter = _FakeEmitter(order=order, store=store)
    emitter.processed_key = _processed_key()
    client = _FakeDeviceLogClient(order=order)
    rec = _make_reconciler(tmp_path, emitter=emitter, store=store, client=client)

    subdir = tmp_path / "aee_exp" / "db.01"
    subdir.mkdir(parents=True)
    payload = _payload(subdir)

    rec._record_intent_placeholder(payload)
    rec._handle_new_entry(payload)

    record = _intents(store)[_LINE]
    assert record["seq_no"] == 1 and record["done"] is True
    assert record["signal_envelope"]["seq_no"] == 1
    assert record["dle_payload"]["id"]
    assert emitter.enqueued[0][0] == 1
    assert client.posts[0]["link_signal_seq_no"] == 1
    # keys 先持久化：enqueue 发生时意图簿已含 seq_no
    assert '"seq_no": 1' in emitter.enqueue_snapshots[0]
    # 顺序：prepare → set(keys/done 之间) → enqueue → dle_post
    assert order.index(("prepare", 1)) < order.index(("enqueue", 1)) < order.index(
        ("dle_post", record["dle_payload"]["id"])
    )

    # done 重入（重拉/竞态）：不再产生任何效果
    rec._handle_new_entry(payload)
    assert len(emitter.enqueued) == 1
    assert len(client.posts) == 1


def test_placeholder_does_not_overwrite_existing_keys(tmp_path):
    """重拉路径会再次触发钩子——占位不得覆盖既有 keys/完成态。"""
    store = _MemStore()
    emitter = _FakeEmitter(store=store)
    emitter.processed_key = _processed_key()
    client = _FakeDeviceLogClient()
    rec = _make_reconciler(tmp_path, emitter=emitter, store=store, client=client)

    subdir = tmp_path / "aee_exp" / "db.01"
    subdir.mkdir(parents=True)
    payload = _payload(subdir)
    rec._record_intent_placeholder(payload)
    rec._handle_new_entry(payload)
    before = _intents(store)[_LINE]

    rec._record_intent_placeholder(payload)   # 重拉/竞态重入

    after = _intents(store)[_LINE]
    assert after["seq_no"] == before["seq_no"]
    assert after["done"] is True
    assert len(emitter.enqueued) == 1


def test_sweep_compensates_processed_entry_without_keys(tmp_path):
    """#1687 折衷版遗留窗口：processed 已写、emit 未发生 → sweep 补偿。"""
    store = _MemStore()
    emitter = _FakeEmitter(store=store)
    emitter.processed_key = _processed_key()
    client = _FakeDeviceLogClient()
    rec = _make_reconciler(tmp_path, emitter=emitter, store=store, client=client)

    subdir = tmp_path / "aee_exp" / "db.01"
    subdir.mkdir(parents=True)
    payload = _payload(subdir)
    rec._record_intent_placeholder(payload)          # 占位（无 keys）
    save_processed_lines(store, _processed_key(), {_LINE})  # processed 已落盘

    replayed = rec._sweep_emit_intents()

    assert replayed == 1
    assert len(emitter.prepared) == 1
    assert len(emitter.enqueued) == 1
    assert len(client.posts) == 1
    assert client.posts[0]["link_signal_seq_no"] == emitter.enqueued[0][0]
    # 重放即完成（done）+ line 已 processed → 同轮清理
    assert _intents(store) == {}
    assert rec._sweep_emit_intents() == 0


def test_sweep_replays_keyed_intent_with_same_keys(tmp_path):
    """崩溃在效果之前/之间：sweep 复用既有 keys，不重新分配。"""
    store = _MemStore()
    emitter = _FakeEmitter(store=store)
    emitter.processed_key = _processed_key()
    client = _FakeDeviceLogClient()
    rec = _make_reconciler(tmp_path, emitter=emitter, store=store, client=client)

    subdir = tmp_path / "aee_exp" / "db.01"
    subdir.mkdir(parents=True)
    payload = _payload(subdir)

    # 手工构造「keys 已持久化、效果未跑」的中间态（崩溃现场）
    seq_no, envelope = emitter.prepare(
        category="AEE", source="reconciler", path_on_device="/data/aee_exp/db.01",
        detected_at=datetime.now(timezone.utc), artifact_uri=str(subdir),
        extra={"schema_version": 2},
    )
    dle_payload = client.build_local_event_payload(
        event_id="11111111-1111-4111-8111-111111111111",
        serial=_SERIAL, event_type="CRASH", link_signal_seq_no=seq_no,
        local_path=subdir, job_id=_JOB_ID,
    )
    record = ei.new_intent_record(
        payload=payload, job_id=_JOB_ID,
        detected_at_iso=datetime.now(timezone.utc).isoformat(),
        entry_origin="runtime",
    )
    record.update({
        "seq_no": seq_no, "signal_envelope": envelope, "dle_payload": dle_payload,
    })
    ei.save_intents(store, _processed_key(), {_LINE: record})
    save_processed_lines(store, _processed_key(), {_LINE})

    assert rec._sweep_emit_intents() == 1

    assert [seq for seq, _ in emitter.enqueued] == [seq_no]     # 复用 seq_no
    assert client.posts[0]["id"] == dle_payload["id"]           # 复用事件 UUID
    assert len(emitter.prepared) == 1                           # 不重新分配


def test_sweep_keeps_done_intent_until_processed(tmp_path):
    """done 但 line 未 processed：保留（重拉会复用 keys），不重复效果。"""
    store = _MemStore()
    emitter = _FakeEmitter(store=store)
    emitter.processed_key = _processed_key()
    client = _FakeDeviceLogClient()
    rec = _make_reconciler(tmp_path, emitter=emitter, store=store, client=client)

    subdir = tmp_path / "aee_exp" / "db.01"
    subdir.mkdir(parents=True)
    payload = _payload(subdir)
    rec._record_intent_placeholder(payload)
    rec._handle_new_entry(payload)   # done，但 processed 尚未写（崩溃点）

    assert rec._sweep_emit_intents() == 0
    assert _LINE in _intents(store)          # 保留
    assert len(emitter.enqueued) == 1        # 未重复效果

    # 重拉（幂等重入）也不重复
    rec._handle_new_entry(payload)
    assert len(emitter.enqueued) == 1
    assert len(client.posts) == 1

    # processed 落盘后清理
    save_processed_lines(store, _processed_key(), {_LINE})
    rec._sweep_emit_intents()
    assert _intents(store) == {}


def test_sweep_replay_attempts_bounded(tmp_path):
    store = _MemStore()
    emitter = _FakeEmitter(store=store, fail_prepare=True)
    emitter.processed_key = _processed_key()
    rec = _make_reconciler(tmp_path, emitter=emitter, store=store)

    subdir = tmp_path / "aee_exp" / "db.01"
    subdir.mkdir(parents=True)
    rec._record_intent_placeholder(_payload(subdir))

    for _ in range(ei.MAX_REPLAY_ATTEMPTS):
        rec._sweep_emit_intents()

    assert _intents(store) == {}                       # 达上限退场
    assert rec.stats.signals_dropped == 1


def test_sweep_covers_baseline_namespace(tmp_path):
    store = _MemStore()
    emitter = _FakeEmitter(store=store)
    emitter.processed_key = _processed_key(prefix="watcher_baseline:901")
    client = _FakeDeviceLogClient()
    rec = _make_reconciler(tmp_path, emitter=emitter, store=store, client=client)

    subdir = tmp_path / "aee_exp" / "db.01"
    subdir.mkdir(parents=True)
    payload = _payload(subdir, origin="baseline")
    payload["state_key_prefix"] = "watcher_baseline:901"
    payload["detected_at_override"] = datetime.now(timezone.utc)
    rec._record_intent_placeholder(payload)
    save_processed_lines(store, _processed_key(prefix="watcher_baseline:901"), {_LINE})

    assert rec._sweep_emit_intents() == 1
    assert len(emitter.enqueued) == 1  # baseline 命名空间同样被 sweep 覆盖
    assert _intents(store, prefix="watcher_baseline:901") == {}


# ----------------------------------------------------------------------
# tick 接线：sweep 在 diff/pull 之前执行；baseline 闭包透传钩子
# ----------------------------------------------------------------------

def test_tick_once_sweeps_emit_intents(tmp_path, monkeypatch):
    from backend.agent.aee.processor import ProcessResult

    store = _MemStore()
    emitter = _FakeEmitter(store=store)
    emitter.processed_key = _processed_key()
    rec = _make_reconciler(tmp_path, emitter=emitter, store=store)

    subdir = tmp_path / "aee_exp" / "db.01"
    subdir.mkdir(parents=True)
    rec._record_intent_placeholder(_payload(subdir))
    save_processed_lines(store, _processed_key(), {_LINE})

    monkeypatch.setattr(
        "backend.agent.aee.reconciler.process_device_logs",
        lambda **kw: ProcessResult(pulled=0),
    )
    rec.tick_once()

    assert len(emitter.enqueued) == 1   # sweep 在 tick 内完成补偿
    assert _intents(store) == {}


def test_baseline_closure_records_intent_in_baseline_namespace(tmp_path, monkeypatch):
    """baseline 闭包透传 on_entry_intent → 占位落在 baseline 命名空间。"""
    from backend.agent.aee.processor import ProcessResult

    store = _MemStore()
    emitter = _FakeEmitter(store=store)
    rec = _make_reconciler(tmp_path, emitter=emitter, store=store)

    subdir = tmp_path / "AEE" / "db.09"
    subdir.mkdir(parents=True)
    line = "/data/aee_exp/db.09,Java (JE),pkg,_,_,_,_,_,com.base,2026-05-28 11:00:00.000"

    def fake_pdl(*, config, on_entry_intent=None, on_new_entry=None, **_):
        assert on_entry_intent is not None and on_new_entry is not None
        payload = _payload(subdir, line=line, origin="baseline")
        payload.pop("state_key_prefix", None)   # 由 processor 透传真实 cfg 前缀
        payload["state_key_prefix"] = config.state_key_prefix
        on_entry_intent(payload)
        on_new_entry(payload)
        return ProcessResult(pulled=1)

    monkeypatch.setattr("backend.agent.aee.reconciler.process_device_logs", fake_pdl)
    rec._run_baseline_snapshot()

    intents = _intents(store, prefix="watcher_baseline:901")
    assert line in intents and intents[line]["done"] is True
    assert intents[line]["detected_at_override"] is not None
    # 共享命名空间不受影响
    assert store.get_state(ei.intent_state_key(_processed_key()), "{}") == "{}"
    assert emitter.enqueued and emitter.enqueued[0][1]["extra"]["entry_origin"] == "baseline"


# ----------------------------------------------------------------------
# emit_intent 模块
# ----------------------------------------------------------------------

def test_intent_book_roundtrip_and_bad_data():
    store = _MemStore()
    key = _processed_key()
    ei.save_intents(store, key, {"a": {"done": True}})
    assert ei.load_intents(store, key) == {"a": {"done": True}}
    assert ei.intent_state_key(key).endswith(":emit_intents")

    store.set_state(ei.intent_state_key(key), "{bad json")
    assert ei.load_intents(store, key) == {}
    store.set_state(ei.intent_state_key(key), "[1,2]")
    assert ei.load_intents(store, key) == {}


def test_new_intent_record_defaults():
    record = ei.new_intent_record(
        payload={
            "line": "L", "parsed": {"a": 1}, "aee_type": "aee_exp",
            "output_subdir": "/x",
        },
        job_id=5, detected_at_iso="2026-01-01T00:00:00+00:00", entry_origin="runtime",
    )
    assert record["seq_no"] is None and record["done"] is False
    assert record["attempts"] == 0 and record["job_id"] == 5


# ----------------------------------------------------------------------
# SignalEmitter prepare/enqueue（LocalDB 幂等）
# ----------------------------------------------------------------------

@pytest.fixture
def local_db(tmp_path):
    db = LocalDB()
    db.initialize(str(tmp_path / "agent.db"))
    yield db
    db.close()


def test_emitter_prepare_does_not_insert_until_enqueue(local_db):
    emitter = SignalEmitter(
        local_db=local_db, job_id=7, host_id="h", device_serial="d",
        fencing_token="f", agent_instance_id="a",
    )
    seq_no, envelope = emitter.prepare(
        category="AEE", source="reconciler", path_on_device="/data/aee_exp/db",
        detected_at=datetime.now(timezone.utc),
    )
    assert local_db.count_pending_log_signals() == 0      # 未落库

    assert emitter.enqueue(seq_no, envelope) is not None
    assert local_db.count_pending_log_signals() == 1
    # 重复 enqueue（崩溃重放）幂等：同一 (job_id, seq_no) 不再插入
    assert emitter.enqueue(seq_no, envelope) is None
    assert local_db.count_pending_log_signals() == 1

    # emit() 仍是 prepare+enqueue 的组合，seq 继续单调
    assert emitter.emit(
        category="AEE", source="reconciler", path_on_device="/data/aee_exp/db2",
    ) == seq_no + 1


# ----------------------------------------------------------------------
# DeviceLogEventClient：payload 组装 + 失败入 outbox 幂等重放
# ----------------------------------------------------------------------

def _client(local_db) -> DeviceLogEventClient:
    return DeviceLogEventClient(
        api_url="http://cp", agent_secret="s", host_id="h1", local_db=local_db,
    )


def test_post_event_payload_failure_enqueues_same_id(local_db, tmp_path):
    client = _client(local_db)
    event_id = "22222222-2222-4222-8222-222222222222"
    payload = client.build_local_event_payload(
        serial="d1", platform="MTK", event_type="KE", event_subtype=None,
        detected_at=datetime.now(timezone.utc), device_timestamp=None,
        local_path=tmp_path, plan_run_id=9, job_id=3, link_signal_seq_no=1,
        event_id=event_id,
    )
    resp = MagicMock(status_code=503, text="down")
    with patch("backend.agent.aee.device_log_event_client.requests.post", return_value=resp):
        assert client.post_event_payload(payload) is None

    pending = local_db.get_pending_dle_registers()
    assert len(pending) == 1
    assert pending[0]["event_id"] == event_id
    assert pending[0]["payload"]["id"] == event_id


def test_emitter_enqueue_prefers_envelope_job_id(local_db):
    """崩溃重放可能发生在后续 Job 的进程：outbox 行要落回 envelope 的原 job。"""
    emitter = SignalEmitter(
        local_db=local_db, job_id=99, host_id="h", device_serial="d",
        fencing_token="f", agent_instance_id="a",
    )
    envelope = {
        "job_id": 7, "seq_no": 3, "host_id": "h", "device_serial": "d",
        "fencing_token": "f", "agent_instance_id": "a", "category": "AEE",
        "source": "reconciler", "path_on_device": "/x",
        "detected_at": "2026-01-01T00:00:00+00:00",
    }
    assert emitter.enqueue(3, envelope) is not None

    rows = local_db.get_pending_log_signals()
    assert len(rows) == 1 and rows[0]["job_id"] == 7


def test_create_pull_failed_event_reuses_preallocated_id(local_db):
    client = _client(local_db)
    event_id = "33333333-3333-4333-8333-333333333333"
    ok = MagicMock(status_code=200)
    ok.json.return_value = {"data": {"event_ids": [event_id]}}
    with patch("backend.agent.aee.device_log_event_client.requests.post", return_value=ok) as post:
        got = client.create_pull_failed_event(
            serial="d1", platform="MTK", event_type="KE", event_subtype=None,
            detected_at=datetime.now(timezone.utc), device_timestamp=None,
            plan_run_id=9, job_id=3, link_signal_seq_no=2, event_id=event_id,
        )
    assert got == event_id
    sent = post.call_args.kwargs["json"]["events"][0]
    assert sent["id"] == event_id and sent["state"] == "PULL_FAILED"


# ----------------------------------------------------------------------
# #1862：跨前缀 done 墓碑回查（迁移丢失窗口不产生新 seq_no）
# ----------------------------------------------------------------------

_BASELINE_PREFIX = f"watcher_baseline:{_JOB_ID}"


def _done_record(seq_no: int) -> Dict[str, Any]:
    return {
        "job_id": _JOB_ID,
        "aee_type": "aee_exp",
        "parsed": {},
        "output_subdir": "",
        "entry_origin": "baseline",
        "detected_at": "2026-05-28T10:00:00+00:00",
        "detected_at_override": None,
        "seq_no": seq_no,
        "signal_envelope": {"seq_no": seq_no},
        "dle_payload": {"id": f"dle-{seq_no}"},
        "done": True,
        "attempts": 0,
    }


def _seed_done(store: _MemStore, prefix: str, seq_no: int) -> None:
    """在指定前缀簿预置同 line 的 done 记录（模拟另一侧已 emit 完成）。"""
    store.set_state(
        ei.intent_state_key(_processed_key(prefix=prefix)),
        json.dumps({_LINE: _done_record(seq_no)}),
    )


def test_baseline_done_tombstone_blocks_new_seq_on_runtime_repull(tmp_path):
    """baseline emit done → 崩溃于 merge 前 → runtime 重拉同 line：
    复用墓碑（seq_no=7）幂等返回，不新建记录、不重复 emit。"""
    store = _MemStore()
    emitter = _FakeEmitter(store=store)
    emitter.processed_key = _processed_key()
    client = _FakeDeviceLogClient()
    rec = _make_reconciler(tmp_path, emitter=emitter, store=store, client=client)
    _seed_done(store, _BASELINE_PREFIX, seq_no=7)

    subdir = tmp_path / "aee_exp" / "db.01"
    subdir.mkdir(parents=True)
    rec._handle_new_entry(_payload(subdir))

    assert emitter.enqueued == [] and client.posts == []
    runtime_book = _intents(store)
    assert runtime_book[_LINE]["done"] is True
    assert runtime_book[_LINE]["seq_no"] == 7  # 原 keys，非新分配


def test_runtime_done_tombstone_blocks_new_seq_on_baseline_rescan(tmp_path):
    """反方向：runtime 已 emit done、baseline 分片重扫同 line——同样幂等。"""
    store = _MemStore()
    emitter = _FakeEmitter(store=store)
    emitter.processed_key = _processed_key()
    client = _FakeDeviceLogClient()
    rec = _make_reconciler(tmp_path, emitter=emitter, store=store, client=client)
    _seed_done(store, "watcher:aee", seq_no=3)

    subdir = tmp_path / "aee_exp" / "db.01"
    subdir.mkdir(parents=True)
    payload = _payload(subdir)
    payload["state_key_prefix"] = _BASELINE_PREFIX
    rec._handle_new_entry(payload)

    assert emitter.enqueued == [] and client.posts == []
    baseline_book = _intents(store, prefix=_BASELINE_PREFIX)
    assert baseline_book[_LINE]["done"] is True
    assert baseline_book[_LINE]["seq_no"] == 3


def test_cross_prefix_lookup_ignores_undone_records(tmp_path):
    """未 done 的跨前缀占位不墓碑化——不影响正常 emit 路径（Revisit 范围）。"""
    store = _MemStore()
    emitter = _FakeEmitter(store=store)
    emitter.processed_key = _processed_key()
    client = _FakeDeviceLogClient()
    rec = _make_reconciler(tmp_path, emitter=emitter, store=store, client=client)
    pending = _done_record(9)
    pending["done"] = False
    pending["seq_no"] = None
    pending["signal_envelope"] = None
    pending["dle_payload"] = None
    store.set_state(
        ei.intent_state_key(_processed_key(prefix=_BASELINE_PREFIX)),
        json.dumps({_LINE: pending}),
    )

    subdir = tmp_path / "aee_exp" / "db.01"
    subdir.mkdir(parents=True)
    rec._handle_new_entry(_payload(subdir))

    # 未复用：runtime 走正常新建 + emit 路径
    assert len(emitter.enqueued) == 1
    runtime_book = _intents(store)
    assert runtime_book[_LINE]["seq_no"] != 9
    assert runtime_book[_LINE]["done"] is True


# ----------------------------------------------------------------------
# #2044：占位写失败不得 finalize（processed 前进 + 无意图 = 静默永久丢失）
# ----------------------------------------------------------------------

class _FlakyIntentStore(_MemStore):
    """只对意图簿键注入写失败（模拟 SQLITE_BUSY / 磁盘满的一瞬）。"""

    def __init__(self, *, fail_times: int = 1, order: Optional[List[tuple]] = None) -> None:
        super().__init__(order=order)
        self.remaining = fail_times
        self.failed_keys: List[str] = []

    def set_state(self, key: str, value: str) -> None:
        if key.endswith(ei.INTENT_KEY_SUFFIX) and self.remaining > 0:
            self.remaining -= 1
            self.failed_keys.append(key)
            raise OSError("database is locked")
        super().set_state(key, value)


def _runtime_hooks(rec: AeeDbHistoryReconciler):
    """复刻 reconciler.tick_once 的 runtime 接线（占位与 emit 同前缀簿）。"""

    def on_intent(payload: Dict[str, Any]) -> None:
        scoped = dict(payload)
        scoped["entry_origin"] = "runtime"
        rec._record_intent_placeholder(scoped)

    def on_entry(payload: Dict[str, Any]) -> None:
        scoped = dict(payload)
        scoped["entry_origin"] = "runtime"
        rec._handle_new_entry(scoped)

    return on_intent, on_entry


def _processed(store: _MemStore, prefix: str = "watcher:aee") -> set:
    from backend.agent.aee.db_history import load_processed_lines

    return load_processed_lines(store, _processed_key(prefix=prefix))


def _pending(store: _MemStore, prefix: str = "watcher:aee") -> Dict[str, Any]:
    return json.loads(
        store.get_state(f"{prefix}:{_SERIAL}:aee_exp:pending_pull", "{}")
    )


def _intent_failure_config():
    from backend.agent.aee.processor import ProcessConfig

    return ProcessConfig(export_mobilelog=False, export_bugreport=False)


def test_intent_placeholder_failure_does_not_finalize_entry(tmp_path, monkeypatch):
    """占位没落盘 ⇒ 该条目不算 finalize：processed 不前进、不 emit、留 pending。"""
    monkeypatch.setenv("STP_AEE_LOCAL_ROOT", str(tmp_path))
    line = "/data/aee_exp/db.90,Java (JE),pkg,_,_,_,_,_,com.example.app,2026-05-28 10:15:22.123"
    _setup_pdl_stubs(monkeypatch, line)
    store = _FlakyIntentStore(fail_times=1)
    emitter = _FakeEmitter(store=store)
    emitter.processed_key = _processed_key()
    client = _FakeDeviceLogClient()
    rec = _make_reconciler(tmp_path, emitter=emitter, store=store, client=client)
    on_intent, on_entry = _runtime_hooks(rec)

    from backend.agent.aee.processor import process_device_logs

    r = process_device_logs(
        serial=_SERIAL, job_id=_JOB_ID, state_store=store, config=_intent_failure_config(),
        on_entry_intent=on_intent, on_new_entry=on_entry,
    )

    assert line not in _processed(store), "占位失败仍推进 processed 即永久丢失"
    assert r.pulled == 0
    assert emitter.prepared == [] and client.posts == [], "意图未落盘就不得产生效果"
    assert store.failed_keys == [ei.intent_state_key(_processed_key())]
    task = _pending(store).get(line) or {}
    assert task.get("retry_count") == 1
    assert str(task.get("last_error")).startswith("emit_intent_placeholder_failed")
    assert any(e.startswith("emit_intent_placeholder_failed") for e in r.errors)
    assert rec.stats.signals_dropped == 0  # 尚未丢弃：仍有重试


def test_intent_placeholder_failure_retries_then_emits_exactly_once(tmp_path, monkeypatch):
    """下一拍廉价重试（本地目录已就位）→ 恰好一次 emit，既不缺也不重。"""
    monkeypatch.setenv("STP_AEE_LOCAL_ROOT", str(tmp_path))
    line = "/data/aee_exp/db.91,Java (JE),pkg,_,_,_,_,_,com.example.app,2026-05-28 10:15:22.123"
    _setup_pdl_stubs(monkeypatch, line)
    store = _FlakyIntentStore(fail_times=1)
    emitter = _FakeEmitter(store=store)
    emitter.processed_key = _processed_key()
    client = _FakeDeviceLogClient()
    rec = _make_reconciler(tmp_path, emitter=emitter, store=store, client=client)
    on_intent, on_entry = _runtime_hooks(rec)

    from backend.agent.aee.processor import process_device_logs

    first = process_device_logs(
        serial=_SERIAL, job_id=_JOB_ID, state_store=store, config=_intent_failure_config(),
        on_entry_intent=on_intent, on_new_entry=on_entry,
    )
    assert line not in _processed(store) and first.pulled == 0

    second = process_device_logs(
        serial=_SERIAL, job_id=_JOB_ID, state_store=store, config=_intent_failure_config(),
        on_entry_intent=on_intent, on_new_entry=on_entry,
    )
    assert line in _processed(store)
    assert second.pulled == 1
    assert [seq for seq, _ in emitter.enqueued] == [1]      # 一个、且只一个 seq
    assert len(client.posts) == 1
    assert _intents(store)[line]["done"] is True
    assert line not in _pending(store)


def test_intent_placeholder_retries_are_bounded_and_report_true_reason(tmp_path, monkeypatch):
    """重试用尽走 on_pull_failed：exhausted + 真因（不是误报的 pull 失败）。"""
    monkeypatch.setenv("STP_AEE_LOCAL_ROOT", str(tmp_path))
    line = "/data/aee_exp/db.92,Java (JE),pkg,_,_,_,_,_,com.example.app,2026-05-28 10:15:22.123"
    _setup_pdl_stubs(monkeypatch, line)
    store = _FlakyIntentStore(fail_times=99)
    store.set_state(
        f"watcher:aee:{_SERIAL}:aee_exp:pending_pull",
        json.dumps({line: {
            "db_path": "/data/aee_exp/db.92",
            "pkg_name": "com.example.app",
            "timestamp": "2026-05-28 10:15:22.123",
            "event_type": "CRASH", "raw_event_type": "Java (JE)",
            "event_subtype": "JE", "retry_count": 10,
            "last_error": "emit_intent_placeholder_failed: OSError",
        }}),
    )
    emitter = _FakeEmitter(store=store)
    emitter.processed_key = _processed_key()
    rec = _make_reconciler(tmp_path, emitter=emitter, store=store)
    on_intent, on_entry = _runtime_hooks(rec)
    captured: Dict[str, Any] = {}

    from backend.agent.aee.processor import process_device_logs

    process_device_logs(
        serial=_SERIAL, job_id=_JOB_ID, state_store=store, config=_intent_failure_config(),
        on_entry_intent=on_intent, on_new_entry=on_entry,
        on_pull_failed=lambda payload: captured.update(payload),
    )

    assert captured.get("exhausted") is True
    assert "emit_intent_placeholder_failed" in str(captured.get("error"))
    assert line in _processed(store) and line not in _pending(store)
    assert emitter.prepared == []


# ----------------------------------------------------------------------
# #2034：跨前缀 done 墓碑回查在生产路径可达 + 墓碑活过 merge 丢失窗口
# ----------------------------------------------------------------------

def test_cross_prefix_tombstone_survives_the_runtime_placeholder(tmp_path, monkeypatch):
    """成因 A：runtime 簿先落占位，回查仍须命中 baseline 墓碑（不新分配 seq）。"""
    monkeypatch.setenv("STP_AEE_LOCAL_ROOT", str(tmp_path))
    _setup_pdl_stubs(monkeypatch, _LINE)
    store = _MemStore()
    # baseline 已 emit done 且已 finalize，但 merge 进 runtime processed 前崩溃
    _seed_done(store, _BASELINE_PREFIX, seq_no=7)
    save_processed_lines(store, _processed_key(prefix=_BASELINE_PREFIX), {_LINE})

    emitter = _FakeEmitter(store=store)
    emitter.processed_key = _processed_key()
    client = _FakeDeviceLogClient()
    rec = _make_reconciler(tmp_path, emitter=emitter, store=store, client=client)
    on_intent, on_entry = _runtime_hooks(rec)

    from backend.agent.aee.processor import process_device_logs

    process_device_logs(
        serial=_SERIAL, job_id=_JOB_ID, state_store=store, config=_intent_failure_config(),
        on_entry_intent=on_intent, on_new_entry=on_entry,
    )

    assert emitter.prepared == [] and client.posts == [], "重复 emit 即双计"
    record = _intents(store)[_LINE]
    assert record["seq_no"] == 7 and record["done"] is True
    assert _LINE in _processed(store)


def test_sweep_keeps_baseline_tombstone_until_runtime_finalizes(tmp_path):
    """成因 B：tick 先 sweep 后拉取——baseline 墓碑不能被本前缀 processed 抹掉。"""
    store = _MemStore()
    _seed_done(store, _BASELINE_PREFIX, seq_no=7)
    save_processed_lines(store, _processed_key(prefix=_BASELINE_PREFIX), {_LINE})
    rec = _make_reconciler(tmp_path, store=store)

    rec._sweep_emit_intents()
    assert _LINE in _intents(store, prefix=_BASELINE_PREFIX), "墓碑须活过 merge 丢失窗口"

    save_processed_lines(store, _processed_key(), {_LINE})   # runtime 已 finalize
    rec._sweep_emit_intents()
    assert _LINE not in _intents(store, prefix=_BASELINE_PREFIX)
