"""LogArchiver 单元测试（ADR-0025 Sprint 2 / S2.1）。

覆盖：归档 happy path / 跳过活跃 job / 跳过未过 grace / 目录树直复制 /
      幂等(已归档跳过) / 注册失败保留本地 / spill_oldest 最旧优先 /
      活跃 Job cycle 快照 / copystat EPERM 容忍。

仅 mock requests.Session（注册 POST）；LocalDB / 文件系统均真实。
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from backend.agent.log_archiver import LogArchiver, ARTIFACT_TYPE_RUN_LOG_BUNDLE
from backend.agent.registry.local_db import LocalDB


@pytest.fixture
def db(tmp_path):
    d = LocalDB()
    d.initialize(str(tmp_path / "agent.db"))
    yield d
    d.close()


@pytest.fixture
def run_log_dir(tmp_path):
    d = tmp_path / "logs" / "runs"
    d.mkdir(parents=True)
    return d


@pytest.fixture
def nfs_dir(tmp_path):
    d = tmp_path / "nfs"
    d.mkdir()
    return d


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


def _make_job_dir(run_log_dir: Path, job_id: int, *, content: bytes = b"log line\n") -> Path:
    job_dir = run_log_dir / str(job_id)
    job_dir.mkdir()
    (job_dir / "init_check.log").write_bytes(content)
    # 回拨 mtime 60s，确保 grace=0 下确定性老化（避免刚建目录 mtime≈now 的浮点边界）
    past = time.time() - 60
    os.utime(job_dir, (past, past))
    return job_dir


def _configure(db, run_log_dir, nfs_dir, *, session=None, grace=0.0) -> LogArchiver:
    return LogArchiver.instance().configure(
        local_db=db,
        host_id="host-test",
        nfs_base_dir=str(nfs_dir),
        run_log_dir=str(run_log_dir),
        api_url="http://fake-backend:8000",
        agent_secret="sek",
        grace_seconds=grace,
        session=session or _ok_session(),
    )


def test_archive_happy_path(db, run_log_dir, nfs_dir):
    sess = _ok_session()
    arch = _configure(db, run_log_dir, nfs_dir, session=sess)
    job_dir = _make_job_dir(run_log_dir, 1001)

    n = arch.scan_once()

    assert n == 1
    # ADR-0025 2026-06-18: 归档与 prune 解耦 —— 复制成功后不立即 prune
    # （本地达阈值才 prune，由 spill_oldest 触发）
    assert job_dir.exists()  # 本地保留（解耦后不立即 prune）
    # NFS 有归档目录树（非 tar）+ manifest
    archived_dirs = list(nfs_dir.glob("archives/*/1001"))
    assert len(archived_dirs) == 1
    assert (archived_dirs[0] / "init_check.log").exists()  # 文件可独立访问
    assert (archived_dirs[0] / "manifest.json").exists()
    # 无 tar.gz 文件
    assert not list(nfs_dir.glob("archives/*/1001/*.tar.gz"))
    # DB 标记
    assert db.is_job_archived(1001) is True
    assert db.count_archived_jobs() == 1
    # 注册 POST：路径 + artifact_type + secret header
    call = sess.post.call_args
    assert call.args[0].endswith("/api/v1/agent/jobs/1001/artifacts")
    assert call.kwargs["json"]["artifact_type"] == ARTIFACT_TYPE_RUN_LOG_BUNDLE
    assert call.kwargs["json"]["storage_uri"] == str(archived_dirs[0])
    assert call.kwargs["headers"]["X-Agent-Secret"] == "sek"


def test_skip_active_job(db, run_log_dir, nfs_dir):
    """ADR-0025 2026-06-18: 活跃 Job 不再跳过，改为 cycle 快照（不 prune、不注册）。"""
    arch = _configure(db, run_log_dir, nfs_dir)
    _make_job_dir(run_log_dir, 2002)
    db.save_active_job(2002, device_id=20, fencing_token="20:1")  # 活跃 → 快照

    n = arch.scan_once()

    assert n == 0  # 快照不计入 archived 数
    assert (run_log_dir / "2002").exists()  # 本地不 prune
    assert db.is_job_archived(2002) is False  # 不注册 JobArtifact
    # NFS 有快照目录
    snapshots = list(nfs_dir.glob("snapshots/*/2002/cycle_0"))
    assert len(snapshots) == 1
    assert (snapshots[0] / "init_check.log").exists()


def test_skip_not_aged(db, run_log_dir, nfs_dir):
    # grace 很大 → 刚创建的目录未过 grace → 跳过
    arch = _configure(db, run_log_dir, nfs_dir, grace=3600.0)
    _make_job_dir(run_log_dir, 3003)

    n = arch.scan_once()

    assert n == 0
    assert (run_log_dir / "3003").exists()


def test_reuse_existing_tar(db, run_log_dir, nfs_dir):
    """ADR-0025 2026-06-18: 不再用 tar，改为目录树直复制。验证目录结构完整复制。"""
    arch = _configure(db, run_log_dir, nfs_dir)
    job_dir = _make_job_dir(run_log_dir, 4004)
    # 在 job 目录内加子目录 + 多文件，验证目录树完整复制
    (job_dir / "subdir").mkdir()
    (job_dir / "subdir" / "nested.log").write_text("nested", encoding="utf-8")

    arch.scan_once()

    archived = next(iter(nfs_dir.glob("archives/*/4004")))
    assert (archived / "init_check.log").exists()
    assert (archived / "subdir" / "nested.log").exists()
    assert (archived / "subdir" / "nested.log").read_text() == "nested"


def test_already_archived_is_idempotent(db, run_log_dir, nfs_dir):
    arch = _configure(db, run_log_dir, nfs_dir)
    _make_job_dir(run_log_dir, 5005)
    db.mark_job_archived(5005, nfs_uri="x", sha256="y", size_bytes=1)

    n = arch.scan_once()

    # 已归档 → 不再归档；残留本地目录被清理
    assert n == 0
    assert not (run_log_dir / "5005").exists()


def test_register_failure_keeps_local(db, run_log_dir, nfs_dir):
    sess = MagicMock()
    resp = MagicMock()
    resp.status_code = 500
    resp.text = "boom"
    sess.post.return_value = resp
    arch = _configure(db, run_log_dir, nfs_dir, session=sess)
    job_dir = _make_job_dir(run_log_dir, 6006)

    n = arch.scan_once()  # archive_one 抛 → scan 计 failed,不计 archived

    assert n == 0
    assert job_dir.exists()                  # 本地保留待重试
    assert db.is_job_archived(6006) is False
    assert arch.snapshot_metrics()["archive_failed"] >= 1


def test_spill_oldest_prefers_oldest(db, run_log_dir, nfs_dir):
    arch = _configure(db, run_log_dir, nfs_dir, grace=3600.0)  # grace 大,正常 scan 不动
    # 3 个 job 目录,人为设 mtime 老→新
    now = time.time()
    for i, job_id in enumerate([7001, 7002, 7003]):
        d = _make_job_dir(run_log_dir, job_id)
        os.utime(d, (now - (300 - i * 100), now - (300 - i * 100)))  # 7001 最旧

    spilled = arch.spill_oldest(max_jobs=2)

    assert spilled == 2
    # 最旧两个(7001,7002)被溢出归档,7003 保留
    assert db.is_job_archived(7001) is True
    assert db.is_job_archived(7002) is True
    assert db.is_job_archived(7003) is False
    assert db.count_spilled_jobs() == 2
    assert (run_log_dir / "7003").exists()


def test_scan_once_grace_zero_archives_immediately(db, run_log_dir, nfs_dir):
    """archive_now control: grace_seconds=0 旁路 aging, 归档刚建且 age=0 的 job 目录;
    但仍跳过 active job。"""
    arch = _configure(db, run_log_dir, nfs_dir, grace=3600.0)
    # 刚建(age≈0) → 默认 grace=3600 应跳过
    jd_t = _make_job_dir(run_log_dir, 9001)
    assert not arch.scan_once()  # 默认 grace → 跳过
    assert not db.is_job_archived(9001)

    # grace_seconds=0 → 过 age 判定 → 归档成功
    n = arch.scan_once(grace_seconds=0.0)
    assert n == 1
    assert db.is_job_archived(9001) is True

    # active job 即使在 grace=0 下也不归档，但做 cycle 快照
    db.save_active_job(9002, device_id=42, fencing_token="t")
    _make_job_dir(run_log_dir, 9002)
    n2 = arch.scan_once(grace_seconds=0.0)
    assert n2 == 0  # 快照不计入 archived
    assert db.is_job_archived(9002) is False
    assert (run_log_dir / "9002").exists()  # 本地不 prune
    # NFS 有快照
    assert len(list(nfs_dir.glob("snapshots/*/9002/cycle_0"))) == 1


def test_archive_survives_nfs_copystat_eperm(db, run_log_dir, nfs_dir, monkeypatch):
    """NFS 回归：归档不应调用 copystat（源元数据 chmod/utime）——在 NFS/CIFS
    挂载上对他属文件会 PermissionError [Errno 1]。本用例把 shutil.copystat 打成
    抛 EPERM：copytree 用 copy_function=copyfile 不触发 copystat → 归档成功。
    复现 ADR-0025 真机 10.36 节点暴露的 log_archiver_archive_failed。
    """
    import shutil as _shutil

    def _boom(*_a, **_k):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(_shutil, "copystat", _boom)

    arch = _configure(db, run_log_dir, nfs_dir, session=_ok_session())
    _make_job_dir(run_log_dir, 7777)

    n = arch.scan_once()

    assert n == 1, "copystat EPERM 不应让归档失败（copytree 用 copyfile 不碰元数据）"
    assert db.is_job_archived(7777) is True
    assert arch.snapshot_metrics()["archive_failed"] == 0
    archived = list(nfs_dir.glob("archives/*/7777"))
    assert len(archived) == 1
    assert (archived[0] / "init_check.log").exists()

