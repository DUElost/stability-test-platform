"""LogArchiver per-job 在途互斥并发测试（ADR-0025 Sprint 2 / 问题2修复）。

scan_once 线程（3600s）与 spill_oldest 线程（300s）可能同时选中同一 job：
本套验证 per-job claim 保证同一 job 不被并发/重复归档、不重复注册、不误计
archive_failed；不同 job 不互相阻塞；claim 下二次确认 is_job_archived 关闭
"他线程刚归档完即释放"的顺序竞态。
"""

from __future__ import annotations

import os
import threading
import time
from unittest.mock import MagicMock

import pytest

from backend.agent.log_archiver import LogArchiver
from backend.agent.registry.local_db import LocalDB


@pytest.fixture
def db(tmp_path):
    d = LocalDB()
    d.initialize(str(tmp_path / "agent.db"))
    yield d
    d.close()


@pytest.fixture(autouse=True)
def reset_singleton():
    LogArchiver._reset_for_tests()
    yield
    LogArchiver._reset_for_tests()


def _ok_session():
    sess = MagicMock()
    resp = MagicMock()
    resp.status_code = 200
    sess.post.return_value = resp
    return sess


def _configure(db, tmp_path, *, session=None):
    run_log_dir = tmp_path / "logs" / "runs"
    run_log_dir.mkdir(parents=True, exist_ok=True)
    nfs_dir = tmp_path / "nfs"
    nfs_dir.mkdir(exist_ok=True)
    arch = LogArchiver.instance().configure(
        local_db=db,
        host_id="h",
        nfs_base_dir=str(nfs_dir),
        run_log_dir=str(run_log_dir),
        api_url="http://fake:8000",
        agent_secret="s",
        grace_seconds=0.0,
        session=session or _ok_session(),
    )
    return arch, run_log_dir


def _make_job_dir(run_log_dir, job_id):
    jd = run_log_dir / str(job_id)
    jd.mkdir(parents=True, exist_ok=True)
    (jd / "run.log").write_bytes(b"x\n")
    past = time.time() - 60  # 回拨 mtime 确保 grace=0 下确定老化
    os.utime(jd, (past, past))
    return jd


def test_claim_blocks_concurrent_same_job(db, tmp_path):
    """持有某 job 的 claim 时，对同一 job 的 archive_one 安静跳过（None），
    不归档、不注册、不计 archive_failed。"""
    arch, run_log_dir = _configure(db, tmp_path)
    jd = _make_job_dir(run_log_dir, 700)

    with arch._claim_archive(700) as got:
        assert got is True
        result = arch.archive_one(700, jd)

    assert result is None
    assert not db.is_job_archived(700)
    assert arch.snapshot_metrics()["archive_failed"] == 0
    arch._session.post.assert_not_called()


def test_claim_allows_different_job(db, tmp_path):
    """持有 job A 的 claim 不阻塞 job B 的归档（不同 job 仍可并发）。"""
    arch, run_log_dir = _configure(db, tmp_path)
    jb = _make_job_dir(run_log_dir, 802)

    with arch._claim_archive(801) as got:
        assert got is True
        uri = arch.archive_one(802, jb)

    assert uri is not None
    assert db.is_job_archived(802)


def test_recheck_archived_under_claim(db, tmp_path):
    """已被他线程归档的 job，archive_one 直呼也经 claim 下二次确认跳过，
    不对已 prune 的目录重打 tar、不重复注册。"""
    arch, run_log_dir = _configure(db, tmp_path)
    jd = _make_job_dir(run_log_dir, 803)
    db.mark_job_archived(803, nfs_uri="nfs://x", sha256="y", size_bytes=1)

    result = arch.archive_one(803, jd)

    assert result is None
    arch._session.post.assert_not_called()


def test_two_threads_archive_same_job_once(db, tmp_path):
    """真线程竞态：A 在 register 阶段阻塞期间，B 并发 archive_one(同 job) 立即跳过；
    A 完成后仅一次注册、仅一次归档。"""
    started = threading.Event()
    release = threading.Event()

    sess = MagicMock()
    resp = MagicMock()
    resp.status_code = 200

    def _blocking_post(*_a, **_k):
        started.set()
        release.wait(timeout=5)
        return resp

    sess.post.side_effect = _blocking_post

    arch, run_log_dir = _configure(db, tmp_path, session=sess)
    jd = _make_job_dir(run_log_dir, 900)

    results: dict = {}

    def _run_a():
        results["a"] = arch.archive_one(900, jd)

    ta = threading.Thread(target=_run_a, name="archiver-A")
    ta.start()
    assert started.wait(timeout=5), "A 应进入阻塞的 register 阶段"

    # B 并发尝试同一 job → claim 失败立即返回 None（不阻塞、不重复注册）
    results["b"] = arch.archive_one(900, jd)

    release.set()
    ta.join(timeout=5)

    assert results["b"] is None, "并发同 job 应被 in-flight claim 跳过"
    assert results["a"] is not None, "持锁线程应完成归档"
    assert db.is_job_archived(900)
    assert sess.post.call_count == 1, "同一 job 只应注册一次"


__all__: list = []
