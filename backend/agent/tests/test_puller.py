"""LogPuller 单元测试（5B1）。

覆盖：
    - submit + worker 成功 pull → on_done(event, enrichment) 被调
    - 成功路径：artifact_uri 指向 NFS 路径 + sha256 正确 + size_bytes + first_lines
    - pull 失败（returncode != 0）：on_done(event, {}) 保证信号不丢
    - adb.pull 抛异常：on_done(event, {}) 不影响主流程
    - 超大文件（> max_file_mb）：on_done 带 size_bytes，artifact_uri=None，本地文件被删
    - 队列满：降级 on_done(event, {})
    - stop(drain=True) 等队列排空
    - stop(drain=False) 直接丢队列，残余 on_done({})
    - submit 在未启动/已停止状态：降级 on_done(event, {})

策略：
    - _FakeAdb 模拟 adb.pull：接受 (serial, remote, local_path)，按预设写本地文件或返回 rc=1
    - on_done 收集 [(event, enrichment)] 便于断言
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from unittest.mock import MagicMock

import pytest

from backend.agent.watcher.puller import LogPuller, PullerStats
from backend.agent.watcher.sources import WatcherEvent


# ----------------------------------------------------------------------
# 辅助
# ----------------------------------------------------------------------

def _evt(
    *,
    category: str = "AEE",
    filename: str = "db.0.0",
    dir_path: str = "/data/aee_exp",
) -> WatcherEvent:
    full_path = f"{dir_path}/{filename}"
    return WatcherEvent(
        category=category,
        event_mask="n",
        dir_path=dir_path,
        filename=filename,
        full_path=full_path,
        detected_at=datetime(2026, 4, 19, 12, 0, 0, tzinfo=timezone.utc),
    )


class _FakeAdb:
    """模拟 AdbWrapper.pull：根据注入策略写本地文件或返回失败。"""

    def __init__(
        self,
        *,
        content_by_remote: Optional[Dict[str, bytes]] = None,
        fail_remotes: Optional[set] = None,
        raise_remotes: Optional[set] = None,
    ) -> None:
        self._content = content_by_remote or {}
        self._fail = fail_remotes or set()
        self._raise = raise_remotes or set()
        self.pull_calls: List[Tuple[str, str, str]] = []

    def pull(self, serial: str, remote: str, local: str):
        self.pull_calls.append((serial, remote, local))
        if remote in self._raise:
            raise RuntimeError(f"adb disconnected: {remote}")
        if remote in self._fail:
            cp = subprocess.CompletedProcess(args=["adb"], returncode=1, stdout="", stderr="err")
            return cp
        # 成功：写本地文件
        data = self._content.get(remote, b"default body\nline 2\nline 3\n")
        Path(local).parent.mkdir(parents=True, exist_ok=True)
        with open(local, "wb") as f:
            f.write(data)
        return subprocess.CompletedProcess(args=["adb"], returncode=0, stdout="", stderr="")


class _Collector:
    """on_pull_done 收集器。"""

    def __init__(self):
        self.calls: List[Tuple[WatcherEvent, Dict[str, Any]]] = []
        self._lock = threading.Lock()

    def __call__(self, event: WatcherEvent, enrichment: Dict[str, Any]) -> None:
        with self._lock:
            self.calls.append((event, enrichment))

    def wait_for(self, n: int, timeout: float = 2.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                if len(self.calls) >= n:
                    return True
            time.sleep(0.02)
        return False


class _TimeoutAwareAdb:
    """记录 LogPuller 传入的 adb pull timeout。"""

    def __init__(self, body: bytes = b"timeout-aware\n") -> None:
        self.body = body
        self.pull_calls: List[Tuple[str, str, str, Optional[float]]] = []

    def pull(
        self,
        serial: str,
        remote: str,
        local: str,
        timeout: Optional[float] = None,
    ):
        self.pull_calls.append((serial, remote, local, timeout))
        Path(local).parent.mkdir(parents=True, exist_ok=True)
        Path(local).write_bytes(self.body)
        return subprocess.CompletedProcess(args=["adb"], returncode=0, stdout="", stderr="")


# ----------------------------------------------------------------------
# 成功路径
# ----------------------------------------------------------------------

def test_submit_success_enriches_envelope(tmp_path):
    body = b"I/AEE_EXP( 1): crash header\nsignal 11 (SIGSEGV)\nbacktrace:\n..."
    adb = _FakeAdb(content_by_remote={"/data/aee_exp/db.0.0": body})
    coll = _Collector()
    p = LogPuller(
        adb=adb, nfs_base_dir=str(tmp_path / "nfs"),
        job_id=42, host_id="HOST", serial="SX",
        on_pull_done=coll,
    )
    p.start()
    try:
        p.submit(_evt(filename="db.0.0"))
        assert coll.wait_for(1, timeout=2.0), "pull 应在 2s 内完成"
    finally:
        p.stop(drain=True, timeout=1.0)

    assert len(coll.calls) == 1
    ev, enr = coll.calls[0]
    assert ev.filename == "db.0.0"
    # 字段齐全
    assert enr["size_bytes"] == len(body)
    assert enr["sha256"] == hashlib.sha256(body).hexdigest()
    assert enr["artifact_uri"] is not None
    assert enr["first_lines"].startswith("I/AEE_EXP")
    # NFS 落盘：<base>/jobs/42/AEE/<epoch_ms>_db.0.0
    local = Path(enr["artifact_uri"])
    assert local.exists()
    assert local.parent.name == "AEE"
    assert local.parent.parent.name == "42"
    assert local.parent.parent.parent.name == "jobs"
    assert local.read_bytes() == body
    # stats
    assert p.stats.pulls_ok == 1
    assert p.stats.pulls_failed == 0


def test_sonic_layout_path(tmp_path):
    """D1: sonic_output_dir 时使用 aee_exp 子目录而非 jobs/<id>/AEE。"""
    body = b"crash-data"
    adb = _FakeAdb(content_by_remote={"/data/aee_exp/db.0.0": body})
    coll = _Collector()
    sonic_dir = tmp_path / "sonic" / "X6851_MonkeyAEEinfo" / "SERIAL1"
    p = LogPuller(
        adb=adb,
        nfs_base_dir=str(tmp_path / "nfs"),
        job_id=99,
        host_id="H",
        serial="S",
        on_pull_done=coll,
        sonic_output_dir=str(sonic_dir),
        bugreport_enabled=False,
    )
    p.start()
    try:
        p.submit(_evt(filename="db.0.0"))
        coll.wait_for(1, timeout=1.5)
    finally:
        p.stop(drain=True, timeout=1.0)

    local = Path(coll.calls[0][1]["artifact_uri"])
    assert local.parent.parent == sonic_dir
    assert local.parent.name == "aee_exp"


def test_default_pull_timeout_matches_aee_processor_budget(tmp_path):
    """Watcher AEE pull 默认超时应与 AEE processor 同量级，避免大 NE 文件在 30s 内被截断。"""
    p = LogPuller(
        adb=_FakeAdb(),
        nfs_base_dir=str(tmp_path / "nfs"),
        job_id=1,
        host_id="H",
        serial="S",
        on_pull_done=_Collector(),
    )

    assert p._pull_timeout == 300.0


    """event.filename 含 / 或空格时，本地路径被规范化。"""
    adb = _FakeAdb(content_by_remote={"/data/aee_exp/weird name@$.log": b"x"})
    coll = _Collector()
    p = LogPuller(
        adb=adb, nfs_base_dir=str(tmp_path / "nfs"),
        job_id=1, host_id="H", serial="S",
        on_pull_done=coll,
    )
    p.start()
    try:
        p.submit(_evt(filename="weird name@$.log"))
        coll.wait_for(1, timeout=1.5)
    finally:
        p.stop(drain=True, timeout=1.0)

    assert len(coll.calls) == 1
    local = Path(coll.calls[0][1]["artifact_uri"])
    # 仅保留安全字符，其余替换为 _
    assert " " not in local.name
    assert "@" not in local.name
    assert "$" not in local.name
    assert local.name.endswith("_weird_name__.log")


# ----------------------------------------------------------------------
# 失败路径
# ----------------------------------------------------------------------

def test_pull_returncode_failure_emits_empty_enrichment(tmp_path):
    adb = _FakeAdb(fail_remotes={"/data/aee_exp/bad.log"})
    coll = _Collector()
    p = LogPuller(
        adb=adb, nfs_base_dir=str(tmp_path / "nfs"),
        job_id=1, host_id="H", serial="S",
        on_pull_done=coll,
    )
    p.start()
    try:
        p.submit(_evt(filename="bad.log"))
        assert coll.wait_for(1, timeout=2.0)
    finally:
        p.stop(drain=True, timeout=1.0)

    assert len(coll.calls) == 1
    _, enr = coll.calls[0]
    assert enr == {}, "失败应 emit 空 enrichment，保证信号不丢 artifact_uri"
    assert p.stats.pulls_failed == 1
    assert p.stats.pulls_ok == 0


def test_pull_timeout_is_forwarded_to_adb(tmp_path):
    adb = _TimeoutAwareAdb()
    coll = _Collector()
    p = LogPuller(
        adb=adb,
        nfs_base_dir=str(tmp_path / "nfs"),
        job_id=1,
        host_id="H",
        serial="S",
        on_pull_done=coll,
        pull_timeout_seconds=123.0,
    )
    p.start()
    try:
        p.submit(_evt(filename="timeout.log"))
        assert coll.wait_for(1, timeout=2.0)
    finally:
        p.stop(drain=True, timeout=1.0)

    assert adb.pull_calls == [
        ("S", "/data/aee_exp/timeout.log", coll.calls[0][1]["artifact_uri"], 123.0)
    ]


def test_pull_raises_exception_emits_empty(tmp_path):
    adb = _FakeAdb(raise_remotes={"/data/aee_exp/boom.log"})
    coll = _Collector()
    p = LogPuller(
        adb=adb, nfs_base_dir=str(tmp_path / "nfs"),
        job_id=1, host_id="H", serial="S",
        on_pull_done=coll,
    )
    p.start()
    try:
        p.submit(_evt(filename="boom.log"))
        assert coll.wait_for(1, timeout=2.0)
    finally:
        p.stop(drain=True, timeout=1.0)

    assert coll.calls[0][1] == {}
    assert p.stats.pulls_failed == 1


def test_oversized_file_emits_size_only(tmp_path):
    big = b"0" * 2048
    adb = _FakeAdb(content_by_remote={"/data/aee_exp/huge.log": big})
    coll = _Collector()
    p = LogPuller(
        adb=adb, nfs_base_dir=str(tmp_path / "nfs"),
        job_id=1, host_id="H", serial="S",
        on_pull_done=coll,
        max_file_mb=0,  # 0 MB = 0 字节上限，任何文件都超限
    )
    # 手动把上限改得更严格：max_file_mb=0 → max_file_bytes=0
    assert p._max_file_bytes == 0
    p.start()
    try:
        p.submit(_evt(filename="huge.log"))
        assert coll.wait_for(1, timeout=2.0)
    finally:
        p.stop(drain=True, timeout=1.0)

    _, enr = coll.calls[0]
    assert enr["artifact_uri"] is None
    assert enr["sha256"] is None
    assert enr["first_lines"] is None
    assert enr["size_bytes"] == len(big)
    assert p.stats.pulls_oversized == 1
    # 本地文件应已删除
    nfs_dir = tmp_path / "nfs" / "jobs" / "1" / "AEE"
    if nfs_dir.exists():
        assert list(nfs_dir.iterdir()) == []


def test_directory_pull_skips_file_enrichment(tmp_path):
    class _DirAdb:
        def pull(self, serial, remote, local, timeout=None):
            target = Path(local)
            target.mkdir(parents=True, exist_ok=True)
            (target / "ZZ_INTERNAL").write_text("meta", encoding="utf-8")
            (target / "crash.dbg").write_bytes(b"dir-crash")
            return subprocess.CompletedProcess(args=["adb"], returncode=0, stdout="", stderr="")

    coll = _Collector()
    p = LogPuller(
        adb=_DirAdb(),
        nfs_base_dir=str(tmp_path / "nfs"),
        job_id=1,
        host_id="H",
        serial="S",
        on_pull_done=coll,
        bugreport_enabled=False,
    )
    p.start()
    try:
        p.submit(_evt(filename="db.22.JE"))
        assert coll.wait_for(1, timeout=2.0)
    finally:
        p.stop(drain=True, timeout=1.0)

    _, enr = coll.calls[0]
    assert enr["artifact_uri"] is not None
    assert Path(enr["artifact_uri"]).is_dir()
    assert enr["sha256"] is None
    assert enr["first_lines"] is None
    assert enr["size_bytes"] is None
    assert p.stats.pulls_ok == 1


# ----------------------------------------------------------------------
# 队列与生命周期
# ----------------------------------------------------------------------

def test_submit_before_start_degrades_to_empty(tmp_path):
    adb = _FakeAdb()
    coll = _Collector()
    p = LogPuller(
        adb=adb, nfs_base_dir=str(tmp_path / "nfs"),
        job_id=1, host_id="H", serial="S",
        on_pull_done=coll,
    )
    # 未 start
    p.submit(_evt())
    assert len(coll.calls) == 1
    assert coll.calls[0][1] == {}
    assert p.stats.submits_dropped == 1


def test_submit_after_stop_degrades_to_empty(tmp_path):
    adb = _FakeAdb()
    coll = _Collector()
    p = LogPuller(
        adb=adb, nfs_base_dir=str(tmp_path / "nfs"),
        job_id=1, host_id="H", serial="S",
        on_pull_done=coll,
    )
    p.start()
    p.stop(drain=False, timeout=0.5)
    # stop 后继续 submit 应降级
    p.submit(_evt())
    # at least 1 degraded call
    assert any(enr == {} for _, enr in coll.calls)


def test_queue_full_degrades_to_empty(tmp_path):
    """队列满时 submit 立即降级；不阻塞生产者。"""
    # 阻塞 pull 以使队列堆积
    block = threading.Event()

    class _BlockingAdb:
        def __init__(self):
            self.pull_calls = 0

        def pull(self, serial, remote, local):
            self.pull_calls += 1
            block.wait(timeout=5.0)
            Path(local).parent.mkdir(parents=True, exist_ok=True)
            Path(local).write_bytes(b"x")
            return subprocess.CompletedProcess(args=["adb"], returncode=0)

    adb = _BlockingAdb()
    coll = _Collector()
    p = LogPuller(
        adb=adb, nfs_base_dir=str(tmp_path / "nfs"),
        job_id=1, host_id="H", serial="S",
        on_pull_done=coll,
        max_workers=1,
        queue_maxsize=2,
    )
    p.start()
    try:
        # 1 条被 worker 立即拿走（阻塞在 pull），2 条塞队列，后续应降级
        for i in range(6):
            p.submit(_evt(filename=f"f{i}.log"))
        # 等所有 submits 至少走完
        deadline = time.time() + 1.0
        while time.time() < deadline and p.stats.submits_total < 6:
            time.sleep(0.02)
        assert p.stats.submits_total == 6
        # 队列满导致的降级应 ≥ 3（worker 占 1 + 队列 2 = 3 个可容纳，剩余 3 降级）
        assert p.stats.submits_dropped >= 3
    finally:
        block.set()
        p.stop(drain=True, timeout=2.0)


def test_stop_drain_true_waits_for_queue(tmp_path):
    """stop(drain=True) 应等队列排空后才返回。"""
    adb = _FakeAdb(content_by_remote={f"/data/aee_exp/a{i}.log": b"x" for i in range(5)})
    coll = _Collector()
    p = LogPuller(
        adb=adb, nfs_base_dir=str(tmp_path / "nfs"),
        job_id=1, host_id="H", serial="S",
        on_pull_done=coll,
        max_workers=2,
    )
    p.start()
    for i in range(5):
        p.submit(_evt(filename=f"a{i}.log"))
    # 不等 collector，直接 drain stop
    p.stop(drain=True, timeout=3.0)
    # drain 完成后所有 5 条都应收到回调
    assert len(coll.calls) == 5
    # 所有都是成功（而非降级）
    assert all(enr.get("artifact_uri") is not None for _, enr in coll.calls)


def test_stop_drain_false_degrades_pending(tmp_path):
    """stop(drain=False) 应立即回调降级剩余事件为空 enrichment。"""
    block = threading.Event()

    class _Blocking:
        def pull(self, serial, remote, local):
            block.wait(timeout=3.0)
            Path(local).parent.mkdir(parents=True, exist_ok=True)
            Path(local).write_bytes(b"x")
            return subprocess.CompletedProcess(args=["adb"], returncode=0)

    coll = _Collector()
    p = LogPuller(
        adb=_Blocking(), nfs_base_dir=str(tmp_path / "nfs"),
        job_id=1, host_id="H", serial="S",
        on_pull_done=coll,
        max_workers=1,
    )
    p.start()
    # 先 submit 1 条被 worker 拿走并阻塞
    p.submit(_evt(filename="blocker.log"))
    time.sleep(0.1)
    # 再 submit 2 条入队列
    p.submit(_evt(filename="pending1.log"))
    p.submit(_evt(filename="pending2.log"))
    # 非 drain 停止：队列 2 条应降级
    p.stop(drain=False, timeout=0.5)
    block.set()   # 放行阻塞中的 pull（避免 join 阻塞永远）

    # 两条 pending 必须以空 enrichment 回调
    filenames_degraded = [
        ev.filename for ev, enr in coll.calls if enr == {}
    ]
    assert "pending1.log" in filenames_degraded
    assert "pending2.log" in filenames_degraded


def test_on_done_exception_does_not_crash_worker(tmp_path):
    """on_done 抛异常时 worker 不崩溃，后续 submit 仍处理。"""
    adb = _FakeAdb(content_by_remote={"/data/aee_exp/ok.log": b"ok"})
    call_count = {"n": 0}
    lock = threading.Lock()

    def bad_on_done(event, enr):
        with lock:
            call_count["n"] += 1
            if event.filename == "crash.log":
                raise RuntimeError("on_done boom")

    p = LogPuller(
        adb=adb, nfs_base_dir=str(tmp_path / "nfs"),
        job_id=1, host_id="H", serial="S",
        on_pull_done=bad_on_done,
        max_workers=1,
    )
    p.start()
    try:
        p.submit(_evt(filename="crash.log"))
        time.sleep(0.2)
        p.submit(_evt(filename="ok.log"))
        deadline = time.time() + 1.5
        while time.time() < deadline and call_count["n"] < 2:
            time.sleep(0.02)
    finally:
        p.stop(drain=True, timeout=1.0)
    assert call_count["n"] == 2, "on_done 异常后 worker 仍应处理下一条"


# ----------------------------------------------------------------------
# first_lines 截断
# ----------------------------------------------------------------------

def test_first_lines_truncated_by_bytes_and_lines(tmp_path):
    """first_lines 按 max_bytes 截断，且最多 max_lines 行。"""
    body = ("line_" + "x" * 50 + "\n") * 500   # 500 行，每行 ~56 字节
    adb = _FakeAdb(content_by_remote={"/data/aee_exp/big.log": body.encode()})
    coll = _Collector()
    p = LogPuller(
        adb=adb, nfs_base_dir=str(tmp_path / "nfs"),
        job_id=1, host_id="H", serial="S",
        on_pull_done=coll,
        first_lines_max_bytes=512,
        first_lines_max_lines=5,
    )
    p.start()
    try:
        p.submit(_evt(filename="big.log"))
        assert coll.wait_for(1, timeout=1.5)
    finally:
        p.stop(drain=True, timeout=1.0)

    _, enr = coll.calls[0]
    fl = enr["first_lines"]
    # 512 字节截断后按行切；无论如何 lines ≤ 5
    lines = fl.splitlines()
    assert len(lines) <= 5
    # 整体 first_lines 长度远小于原始
    assert len(fl) < len(body)


# ----------------------------------------------------------------------
# T0.5-3: bugreport event_type 映射 (P0-#3 修复)
# ----------------------------------------------------------------------

def test_aee_pull_triggers_bugreport_with_mapped_crash_event_type(tmp_path, monkeypatch):
    """T0.5-3 P0-#3: AEE category 必须映射成 event_type='CRASH',否则 cooldown 早返回 → bugreport 永不导出。"""
    body = b"crash-body"
    adb = _FakeAdb(content_by_remote={"/data/aee_exp/db.0.0": body})
    coll = _Collector()
    sonic_dir = tmp_path / "sonic" / "X_MonkeyAEEinfo" / "S1"
    sonic_dir.mkdir(parents=True, exist_ok=True)

    captured: dict = {}

    def fake_export(**kw):
        captured.update(kw)
        return True

    import backend.agent.watcher.puller as puller_mod
    # _maybe_export_bugreport 内部 lazy import,需 patch aee.bugreport 模块对象
    from backend.agent.aee import bugreport as bugreport_mod
    monkeypatch.setattr(bugreport_mod, "export_bugreport_for_timestamp", fake_export)

    p = LogPuller(
        adb=adb,
        nfs_base_dir=str(tmp_path / "nfs"),
        job_id=1,
        host_id="H",
        serial="S",
        on_pull_done=coll,
        sonic_output_dir=str(sonic_dir),
        bugreport_enabled=True,
    )
    p.start()
    try:
        p.submit(_evt(category="AEE", filename="db.0.0", dir_path="/data/aee_exp"))
        assert coll.wait_for(1, timeout=2.0)
    finally:
        p.stop(drain=True, timeout=1.0)

    assert captured, "bugreport export 必须被调用"
    assert captured.get("event_type") == "CRASH", (
        f"AEE category 必须映射成 event_type='CRASH',实际收到 {captured.get('event_type')!r}"
    )
    assert captured.get("serial") == "S"


def test_vendor_aee_pull_triggers_bugreport_with_mapped_crash_event_type(tmp_path, monkeypatch):
    """T0.5-3 P0-#3: VENDOR_AEE category 同样映射为 CRASH。"""
    body = b"vendor-crash"
    adb = _FakeAdb(content_by_remote={"/data/vendor/aee_exp/db.5": body})
    coll = _Collector()
    sonic_dir = tmp_path / "sonic" / "X_MonkeyAEEinfo" / "S2"
    sonic_dir.mkdir(parents=True, exist_ok=True)

    captured: dict = {}

    def fake_export(**kw):
        captured.update(kw)
        return True

    from backend.agent.aee import bugreport as bugreport_mod
    monkeypatch.setattr(bugreport_mod, "export_bugreport_for_timestamp", fake_export)

    p = LogPuller(
        adb=adb,
        nfs_base_dir=str(tmp_path / "nfs"),
        job_id=2,
        host_id="H",
        serial="S2",
        on_pull_done=coll,
        sonic_output_dir=str(sonic_dir),
        bugreport_enabled=True,
    )
    p.start()
    try:
        p.submit(_evt(category="VENDOR_AEE", filename="db.5", dir_path="/data/vendor/aee_exp"))
        assert coll.wait_for(1, timeout=2.0)
    finally:
        p.stop(drain=True, timeout=1.0)

    assert captured.get("event_type") == "CRASH"


def test_anr_event_not_routed_to_bugreport_under_aee_guard(tmp_path, monkeypatch):
    """T0.5-3 边界: 现行 guard `event.category in ("AEE", "VENDOR_AEE")` 只放行 AEE 系;
    ANR 不会进入 _maybe_export_bugreport(由其他路径处理)。
    """
    body = b"anr-trace"
    adb = _FakeAdb(content_by_remote={"/data/anr/trace_00": body})
    coll = _Collector()
    sonic_dir = tmp_path / "sonic" / "X_MonkeyAEEinfo" / "S3"
    sonic_dir.mkdir(parents=True, exist_ok=True)

    call_count = {"n": 0}

    def fake_export(**kw):
        call_count["n"] += 1
        return True

    from backend.agent.aee import bugreport as bugreport_mod
    monkeypatch.setattr(bugreport_mod, "export_bugreport_for_timestamp", fake_export)

    p = LogPuller(
        adb=adb,
        nfs_base_dir=str(tmp_path / "nfs"),
        job_id=3,
        host_id="H",
        serial="S3",
        on_pull_done=coll,
        sonic_output_dir=str(sonic_dir),
        bugreport_enabled=True,
    )
    p.start()
    try:
        p.submit(_evt(category="ANR", filename="trace_00", dir_path="/data/anr"))
        assert coll.wait_for(1, timeout=2.0)
    finally:
        p.stop(drain=True, timeout=1.0)

    assert call_count["n"] == 0, "ANR 不应进入 _maybe_export_bugreport(guard 只放 AEE/VENDOR_AEE)"
