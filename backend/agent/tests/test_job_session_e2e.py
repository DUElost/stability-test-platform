"""阶段 6 — JobSession 真实闭环集成测试。

验证目标（覆盖运行期主链）：
    JobSession.__enter__
        → LogWatcherManager.start
            → 真实 CapabilityProber + DeviceLogWatcher + LogPuller
                → 真实 InotifydSource(_FakePopen 喂事件)
                → 真实 SignalEmitter → LocalDB(SQLite WAL)
    JobSession.__exit__
        → DeviceLogWatcher.stop(drain=True)
            → batcher drain → AEE 走 puller、ANR 直接 emit
        → 锁释放（Phase 2 必定执行）
    OutboxDrainer.tick_once
        → 真实读取 LocalDB log_signal_outbox → POST /api/v1/agent/log-signals
            → 仅 mock requests.Session

仅 mock 三个外部边界：
    1. AdbWrapper（_FakeAdb：模拟 adb shell + adb pull）
    2. subprocess.Popen（_FakePopen：模拟 inotifyd 子进程）
    3. requests.Session（mock 200 响应；不 mock outbox 自身逻辑）

不 mock：
    - LogWatcherManager / DeviceLogWatcher / LogPuller / SignalEmitter
    - LocalDB / WatcherPolicy / CapabilityProber / OutboxDrainer
    - JobSession 本体

覆盖场景（7 个）：
    1. AEE happy path → puller pull → envelope 含 artifact_uri/sha256 → outbox → HTTP 推送
    2. ANR happy path → batcher 聚合 → outbox 无 enrichment
    3. NFS 禁用兼容（nfs_base_dir="" → puller 不挂载，AEE 仍走 immediate emit）
    4. 退出 drain 残余 ANR（exit_drain_timeout 内 flush 到 outbox）
    5. policy=DEGRADED + probe 失败 → JobSession 不抛 + capability=unavailable + 锁仍释放
    6. policy=FAIL + probe 失败 → JobSession 抛 JobStartupError + 锁立即释放
    7. watcher.start 中 source spawn 失败 → puller/batcher 都回滚（无孤儿线程）+ 锁释放
"""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest

from backend.agent.job_session import JobSession, JobStartupError
from backend.agent.registry.local_db import LocalDB
from backend.agent.watcher import LogWatcherManager
from backend.agent.watcher.emitter import OutboxDrainer
from backend.agent.tests.test_sources import _FakeAdb, _FakePopen


# ----------------------------------------------------------------------
# 公共 fixtures
# ----------------------------------------------------------------------

@pytest.fixture
def db(tmp_path):
    """每个用例独立 SQLite 实例（WAL 文件随 tmp_path 一起清理）。"""
    db = LocalDB()
    db.initialize(str(tmp_path / "agent.db"))
    yield db
    db.close()


@pytest.fixture
def nfs_dir(tmp_path):
    """puller 用的 NFS 根目录（真实文件系统，便于断言落盘）。"""
    d = tmp_path / "nfs"
    d.mkdir()
    return d


@pytest.fixture(autouse=True)
def reset_singletons():
    """每个用例前后强制重置 LogWatcherManager + OutboxDrainer 单例。

    关键：JobSession 直接通过 LogWatcherManager.instance() 拿单例，所以必须
    用单例机制本身的 _reset_for_tests 钩子，而不能 monkeypatch。
    """
    LogWatcherManager._reset_for_tests()
    OutboxDrainer._reset_for_tests()
    yield
    LogWatcherManager._reset_for_tests()
    OutboxDrainer._reset_for_tests()


@pytest.fixture
def lock_tracker():
    """追踪 lock_register/deregister 调用（验证 Phase 2 必定执行）。"""

    class Tracker:
        def __init__(self):
            self.active_jobs: set = set()
            self.active_devices: set = set()

        def reg_job(self, jid: int) -> None:
            self.active_jobs.add(jid)

        def dereg_job(self, jid: int) -> None:
            self.active_jobs.discard(jid)

        def reg_dev(self, did: int) -> None:
            self.active_devices.add(did)

        def dereg_dev(self, did: int) -> None:
            self.active_devices.discard(did)

    return Tracker()


# ----------------------------------------------------------------------
# 共享 helpers
# ----------------------------------------------------------------------

def _adb_with_root_probe(serial: str, *, dirs: List[str]) -> _FakeAdb:
    """构造一个能通过 CapabilityProber 探测的 _FakeAdb（root + 所有目录可读）。"""
    adb = _FakeAdb()
    adb.on(serial, "id", stdout="uid=0(root) gid=0(root)")
    adb.on(serial, "which inotifyd", stdout="/system/bin/inotifyd")
    for d in dirs:
        adb.on(serial, f"ls -d {d}", stdout=d)
    return adb


def _adb_unavailable(serial: str) -> _FakeAdb:
    """构造一个会让 CapabilityProber 返回 UNAVAILABLE 的 _FakeAdb（所有目录都失败）。"""
    adb = _FakeAdb()
    adb.on(serial, "id", stdout="uid=2000(shell)")  # 非 root
    adb.on(serial, "which inotifyd", stdout="")  # inotifyd 不可用
    # 不 register ls 规则 → ls 抛异常 → 所有目录被标记为不可访问
    return adb


def _make_payload(
    *,
    job_id: int = 1001,
    device_id: int = 42,
    serial: str = "SERIAL-E2E",
    watcher_policy: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """构造 JobSession 需要的最小合法 payload。"""
    payload: Dict[str, Any] = {
        "id": job_id,
        "device_id": device_id,
        "device_serial": serial,
        "host_id": "host-e2e",
        "pipeline_def": {"stages": {"prepare": [], "execute": [], "post_process": []}},
    }
    if watcher_policy is not None:
        payload["watcher_policy"] = watcher_policy
    return payload


def _install_pull_writer(adb: _FakeAdb, *, fixed_content: bytes = b"crash dump line 1\nline 2\n"):
    """给 _FakeAdb 装上 pull 方法：写 fixed_content 到 local_path 并返回 returncode=0。

    LogPuller._do_pull 调用 self._adb.pull(serial, remote, local) 期望返回带
    returncode 的对象。这里 monkey-patch 实例方法即可（不污染 _FakeAdb 类）。
    """

    def pull(serial: str, remote: str, local: str):
        Path(local).parent.mkdir(parents=True, exist_ok=True)
        Path(local).write_bytes(fixed_content)
        result = MagicMock()
        result.returncode = 0
        return result

    adb.pull = pull
    return adb


def _wait_pending(db: LocalDB, expected: int, *, timeout: float = 3.0) -> List[Dict[str, Any]]:
    """轮询 outbox 直到至少 expected 条；超时返回当前快照（不 raise，让断言报具体差异）。"""
    deadline = time.time() + timeout
    rows: List[Dict[str, Any]] = []
    while time.time() < deadline:
        rows = db.get_pending_log_signals()
        if len(rows) >= expected:
            return rows
        time.sleep(0.05)
    return rows


def _make_mock_session(status_code: int = 200):
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.raise_for_status.return_value = None
    sess = MagicMock()
    sess.post.return_value = mock_resp
    return sess


# ----------------------------------------------------------------------
# TC-1：AEE 完整闭环（核心 happy path）
# ----------------------------------------------------------------------

def test_e2e_aee_event_flows_through_puller_to_outbox_and_http(
    db, nfs_dir, lock_tracker
):
    """JobSession→Manager→Watcher→Puller→Emitter→Outbox→Drainer→HTTP 全链路。

    关键不变量：
        - artifact_uri 指向 NFS 实际文件
        - sha256 / size_bytes / first_lines 全部填入 envelope
        - drainer 把信号 POST 到 /api/v1/agent/log-signals
        - 锁在 exit 后释放
    """
    serial = "SERIAL-E2E-1"
    adb = _adb_with_root_probe(serial, dirs=["/data/anr", "/data/aee_exp"])
    _install_pull_writer(adb, fixed_content=b"AEE crash header\nstack frame 1\nstack frame 2\n")

    LogWatcherManager.instance().configure(
        adb=adb,
        adb_path="adb",
        local_db=db,
        api_url="http://fake-backend:8000",
        agent_secret="test-secret",
        nfs_base_dir=str(nfs_dir),
    )

    aee_lines = ["n\t/data/aee_exp\tdb.0.0\n"]
    fake_popen = _FakePopen(aee_lines)

    with patch("backend.agent.watcher.sources.subprocess.Popen", return_value=fake_popen):
        with JobSession(
            job_payload=_make_payload(job_id=1001, device_id=42, serial=serial),
            host_id="host-e2e",
            log_dir=str(nfs_dir / "jobs" / "1001"),
            lock_register=lock_tracker.reg_job,
            lock_deregister=lock_tracker.dereg_job,
            device_id_register=lock_tracker.reg_dev,
            device_id_deregister=lock_tracker.dereg_dev,
        ) as session:
            # 锁已注册
            assert 1001 in lock_tracker.active_jobs
            assert 42 in lock_tracker.active_devices
            # capability 实际 probe 成功
            assert session.summary.watcher_capability == "inotifyd_root"
            # 等 inotifyd reader → batcher → puller → emit
            rows = _wait_pending(db, expected=1, timeout=3.0)
            assert len(rows) == 1, f"应至少有 1 条 AEE 信号；实际 {len(rows)}"

    # exit 后锁必释放
    assert 1001 not in lock_tracker.active_jobs
    assert 42 not in lock_tracker.active_devices

    # envelope 富化字段全在
    rows = db.get_pending_log_signals()
    assert len(rows) == 1
    env = rows[0]["envelope"]
    assert env["category"] == "AEE"
    assert env["path_on_device"] == "/data/aee_exp/db.0.0"
    assert env["artifact_uri"] is not None, "puller 必须写入 artifact_uri"
    assert env["artifact_uri"].endswith("db.0.0")
    assert Path(env["artifact_uri"]).exists(), "NFS 实文件必须存在"
    assert env["sha256"] is not None and len(env["sha256"]) == 64
    assert env["size_bytes"] == len(b"AEE crash header\nstack frame 1\nstack frame 2\n")
    assert "AEE crash header" in env["first_lines"]

    # OutboxDrainer 真实推送（仅 mock requests.Session）
    sess = _make_mock_session(200)
    drainer = OutboxDrainer.instance().configure(
        local_db=db,
        api_url="http://fake-backend:8000",
        agent_secret="test-secret",
        session=sess,
    )
    flushed = drainer.tick_once()
    assert flushed == 1
    assert len(db.get_pending_log_signals()) == 0, "推送成功后 outbox 应清空"
    # POST shape 校验（契约：路径 + secret header）
    call = sess.post.call_args
    assert call.args[0].endswith("/api/v1/agent/log-signals")
    assert call.kwargs["headers"]["X-Agent-Secret"] == "test-secret"


# ----------------------------------------------------------------------
# TC-2：ANR 走 batcher 不走 puller
# ----------------------------------------------------------------------

def test_e2e_anr_event_flows_through_batcher_without_puller_enrichment(
    db, nfs_dir, lock_tracker
):
    """ANR 事件量大 + 文件短 → 走批量路径，envelope 不含 artifact_uri。

    设计取舍验证：5B1 puller 仅富化 AEE / VENDOR_AEE；ANR 不走 puller。
    """
    serial = "SERIAL-E2E-2"
    adb = _adb_with_root_probe(serial, dirs=["/data/anr", "/data/aee_exp"])
    # 即使装了 pull writer，ANR 也不应触发 pull
    _install_pull_writer(adb)

    LogWatcherManager.instance().configure(
        adb=adb,
        adb_path="adb",
        local_db=db,
        api_url="http://fake-backend:8000",
        agent_secret="",
        nfs_base_dir=str(nfs_dir),
    )

    anr_lines = [
        "n\t/data/anr\ttrace_01.txt\n",
        "n\t/data/anr\ttrace_02.txt\n",
    ]
    fake_popen = _FakePopen(anr_lines)

    with patch("backend.agent.watcher.sources.subprocess.Popen", return_value=fake_popen):
        with JobSession(
            job_payload=_make_payload(
                job_id=1002, serial=serial,
                # 缩短 batch_interval 让用例不用等太久
                watcher_policy={"batch_interval_seconds": 0.3},
            ),
            host_id="host-e2e",
            log_dir=str(nfs_dir / "jobs" / "1002"),
            lock_register=lock_tracker.reg_job,
            lock_deregister=lock_tracker.dereg_job,
        ):
            rows = _wait_pending(db, expected=2, timeout=3.0)
            assert len(rows) == 2, "ANR 经 batcher flush 后应有 2 条信号"

    rows = db.get_pending_log_signals()
    assert len(rows) == 2
    for r in rows:
        env = r["envelope"]
        assert env["category"] == "ANR"
        # 关键：ANR 不走 puller → 没有 artifact_uri / sha256 / size_bytes
        assert env.get("artifact_uri") is None
        assert env.get("sha256") is None
        assert env.get("size_bytes") is None

    # NFS 目录下也不应该被 puller 写入（因为 ANR 根本不入队）
    anr_files = list((nfs_dir / "jobs" / "1002" / "ANR").glob("*")) \
        if (nfs_dir / "jobs" / "1002" / "ANR").exists() else []
    assert anr_files == [], "ANR 不应触发 NFS 写入"


# ----------------------------------------------------------------------
# TC-3：NFS 禁用兼容（puller 关闭）
# ----------------------------------------------------------------------

def test_e2e_nfs_disabled_aee_still_emits_without_enrichment(
    db, nfs_dir, lock_tracker
):
    """nfs_base_dir="" → manager 不构造 puller，AEE 仍走 immediate emit。

    向后兼容关键点：NFS 配置缺失时整个 watcher 子系统仍然能运行，
    只是 envelope 无 artifact_uri / sha256（运维降级路径）。
    """
    serial = "SERIAL-E2E-3"
    adb = _adb_with_root_probe(serial, dirs=["/data/anr", "/data/aee_exp"])

    LogWatcherManager.instance().configure(
        adb=adb,
        adb_path="adb",
        local_db=db,
        api_url="http://fake-backend:8000",
        agent_secret="",
        nfs_base_dir="",  # 关键：禁用 puller
    )

    aee_lines = ["n\t/data/aee_exp\tdb.0.5\n"]
    fake_popen = _FakePopen(aee_lines)

    with patch("backend.agent.watcher.sources.subprocess.Popen", return_value=fake_popen):
        with JobSession(
            job_payload=_make_payload(job_id=1003, serial=serial),
            host_id="host-e2e",
            log_dir=str(nfs_dir / "jobs" / "1003"),
            lock_register=lock_tracker.reg_job,
            lock_deregister=lock_tracker.dereg_job,
        ):
            rows = _wait_pending(db, expected=1, timeout=2.0)
            assert len(rows) == 1, "NFS 禁用时 AEE 应仍 emit（无 enrichment）"

    rows = db.get_pending_log_signals()
    env = rows[0]["envelope"]
    assert env["category"] == "AEE"
    assert env["path_on_device"] == "/data/aee_exp/db.0.5"
    # 关键：puller 未挂载 → 无 enrichment
    assert env.get("artifact_uri") is None
    assert env.get("sha256") is None


# ----------------------------------------------------------------------
# TC-4：stop 时 drain 残余 ANR
# ----------------------------------------------------------------------

def test_e2e_exit_drain_flushes_pending_anr_to_outbox(db, nfs_dir, lock_tracker):
    """JobSession.__exit__ 必须把 batcher 内残余 ANR flush 到 outbox（exit_drain_timeout 内）。

    场景：batch_interval=60s 故意大 → 不会自然触发 → 只能靠 stop(drain=True) flush。
    """
    serial = "SERIAL-E2E-4"
    adb = _adb_with_root_probe(serial, dirs=["/data/anr", "/data/aee_exp"])

    LogWatcherManager.instance().configure(
        adb=adb,
        adb_path="adb",
        local_db=db,
        api_url="http://fake-backend:8000",
        agent_secret="",
        nfs_base_dir=str(nfs_dir),
    )

    anr_lines = [
        "n\t/data/anr\tdrain_a\n",
        "n\t/data/anr\tdrain_b\n",
    ]
    fake_popen = _FakePopen(anr_lines)

    with patch("backend.agent.watcher.sources.subprocess.Popen", return_value=fake_popen):
        with JobSession(
            job_payload=_make_payload(
                job_id=1004, serial=serial,
                watcher_policy={
                    "batch_interval_seconds": 60.0,  # 大到不会自然触发
                    "batch_max_events": 100,
                    "exit_drain_timeout_seconds": 3.0,
                },
            ),
            host_id="host-e2e",
            log_dir=str(nfs_dir / "jobs" / "1004"),
            lock_register=lock_tracker.reg_job,
            lock_deregister=lock_tracker.dereg_job,
        ) as session:
            # 等 inotifyd reader 把事件读进 batcher（但还没 flush）
            time.sleep(0.5)
            # exit 之前 outbox 应该是空的（batch 还没到 60s）
            assert len(db.get_pending_log_signals()) == 0, \
                "60s batch_interval 不应在 0.5s 内触发"

    # exit 后 drain 必须把 2 条 flush 到 outbox
    rows = db.get_pending_log_signals()
    assert len(rows) == 2, f"drain 后应有 2 条；实际 {len(rows)}"
    paths = sorted(r["envelope"]["path_on_device"] for r in rows)
    assert paths == ["/data/anr/drain_a", "/data/anr/drain_b"]
    # Phase 2 锁释放
    assert 1004 not in lock_tracker.active_jobs


# ----------------------------------------------------------------------
# TC-5：DEGRADED 真实 probe 失败路径
# ----------------------------------------------------------------------

def test_e2e_degraded_policy_continues_when_probe_unavailable(
    db, nfs_dir, lock_tracker
):
    """policy.on_unavailable=degraded（首发默认） + probe 失败 → JobSession 不抛 + capability=unavailable + 锁仍释放。

    真实路径：CapabilityProber 用 _adb_unavailable 探测会返回 UNAVAILABLE，
    Manager.start Step 3 走 DEGRADED 分支，不创建 DeviceLogWatcher。
    """
    serial = "SERIAL-E2E-5"
    adb = _adb_unavailable(serial)

    LogWatcherManager.instance().configure(
        adb=adb,
        adb_path="adb",
        local_db=db,
        api_url="http://fake-backend:8000",
        agent_secret="",
        nfs_base_dir=str(nfs_dir),
    )

    with JobSession(
        job_payload=_make_payload(
            job_id=1005, serial=serial,
            watcher_policy={"on_unavailable": "degraded"},
        ),
        host_id="host-e2e",
        log_dir=str(nfs_dir / "jobs" / "1005"),
        lock_register=lock_tracker.reg_job,
        lock_deregister=lock_tracker.dereg_job,
        device_id_register=lock_tracker.reg_dev,
        device_id_deregister=lock_tracker.dereg_dev,
    ) as session:
        # DEGRADED 不抛
        assert session.summary.watcher_capability == "unavailable"
        # 锁仍保留（DEGRADED 下 Job 继续执行）
        assert 1005 in lock_tracker.active_jobs

    # exit 后 Phase 2 释放锁
    assert 1005 not in lock_tracker.active_jobs
    assert 42 not in lock_tracker.active_devices
    # 没有任何信号产生
    assert db.get_pending_log_signals() == []


# ----------------------------------------------------------------------
# TC-6：FAIL 真实 probe 失败 → JobSession 抛 + 锁释放
# ----------------------------------------------------------------------

def test_e2e_fail_policy_raises_jobstartuperror_and_releases_lock(
    db, nfs_dir, lock_tracker
):
    """policy.on_unavailable=fail + probe 失败 → JobStartupError(reason_code=watcher_probe_failed) + 锁立即释放。"""
    serial = "SERIAL-E2E-6"
    adb = _adb_unavailable(serial)

    LogWatcherManager.instance().configure(
        adb=adb,
        adb_path="adb",
        local_db=db,
        api_url="http://fake-backend:8000",
        agent_secret="",
        nfs_base_dir=str(nfs_dir),
    )

    with pytest.raises(JobStartupError) as excinfo:
        with JobSession(
            job_payload=_make_payload(
                job_id=1006, serial=serial,
                watcher_policy={"on_unavailable": "fail"},
            ),
            host_id="host-e2e",
            log_dir=str(nfs_dir / "jobs" / "1006"),
            lock_register=lock_tracker.reg_job,
            lock_deregister=lock_tracker.dereg_job,
            device_id_register=lock_tracker.reg_dev,
            device_id_deregister=lock_tracker.dereg_dev,
        ):
            pytest.fail("JobSession 进入 with 主体 — 应在 __enter__ 抛异常")

    assert excinfo.value.reason_code == "watcher_probe_failed"
    # FAIL 路径必须立即释放锁（即使 with 都没进入）
    assert 1006 not in lock_tracker.active_jobs
    assert 42 not in lock_tracker.active_devices


# ----------------------------------------------------------------------
# TC-7：watcher.start spawn 失败 → 全链路回滚
# ----------------------------------------------------------------------

def test_e2e_source_spawn_failure_rolls_back_puller_and_releases_lock(
    db, nfs_dir, lock_tracker
):
    """probe 通过但 InotifydSource Popen 抛异常 → DeviceLogWatcher.start 回滚 puller + batcher，
    Manager 抛 WatcherStartError → 上层 policy=DEGRADED 时 JobSession 不抛但锁会被释放。

    关键不变量：失败时不能留 daemon 线程孤儿（puller worker / batcher flusher）。
    """
    serial = "SERIAL-E2E-7"
    adb = _adb_with_root_probe(serial, dirs=["/data/anr", "/data/aee_exp"])
    _install_pull_writer(adb)

    LogWatcherManager.instance().configure(
        adb=adb,
        adb_path="adb",
        local_db=db,
        api_url="http://fake-backend:8000",
        agent_secret="",
        nfs_base_dir=str(nfs_dir),
    )

    threads_before = {t.name for t in threading.enumerate()}

    # patch Popen 抛 OSError → InotifydSource.start 抛 RuntimeError → watcher.start 回滚
    with patch(
        "backend.agent.watcher.sources.subprocess.Popen",
        side_effect=OSError("simulated adb binary missing"),
    ):
        # DEGRADED 默认：watcher.start 失败被吞 → JobSession 不抛
        with JobSession(
            job_payload=_make_payload(
                job_id=1007, serial=serial,
                watcher_policy={"on_unavailable": "degraded"},
            ),
            host_id="host-e2e",
            log_dir=str(nfs_dir / "jobs" / "1007"),
            lock_register=lock_tracker.reg_job,
            lock_deregister=lock_tracker.dereg_job,
            device_id_register=lock_tracker.reg_dev,
            device_id_deregister=lock_tracker.dereg_dev,
        ) as session:
            # capability fall back to unavailable（manager 内部 record state='failed' 然后吞）
            assert session.summary.watcher_capability == "unavailable"
            # 锁仍保留
            assert 1007 in lock_tracker.active_jobs

    # Phase 2 锁释放
    assert 1007 not in lock_tracker.active_jobs
    assert 42 not in lock_tracker.active_devices

    # 关键：回滚后不应留任何 puller / batcher 孤儿线程
    # 给 daemon 线程一点时间退出（batcher flusher 是 0.1s 检查 stop_evt）
    deadline = time.time() + 1.5
    while time.time() < deadline:
        active_names = {t.name for t in threading.enumerate()} - threads_before
        leaked = [
            n for n in active_names
            if n.startswith(("puller-", "batcher-flusher-", "inotifyd-reader-"))
        ]
        if not leaked:
            break
        time.sleep(0.1)
    leftover = [
        t.name for t in threading.enumerate()
        if t.name not in threads_before
        and t.name.startswith(("puller-", "batcher-flusher-", "inotifyd-reader-"))
    ]
    assert leftover == [], f"watcher.start 失败回滚后不应留下线程：{leftover}"
