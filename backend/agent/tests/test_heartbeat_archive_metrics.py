"""心跳归档指标聚合测试（ADR-0025 Sprint 2 / 问题1修复）。

覆盖 collect_archive_heartbeat_metrics()：
  - 归档子系统未配置 → None（心跳不含 archive 段，archive-status agent_metrics=null）
  - 仅 LogArchiver 配置 → 返回 archiver 指标，无磁盘字段
  - LogArchiver + LocalDiskMonitor 均配置 → 合并出完整字段
  - pending_archive 走缓存：scan_once 刷新后体现在聚合结果（Task1↔Task2 集成）

仅 mock requests.Session + disk_usage_fn；LocalDB / 文件系统 / tar 真实。
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from backend.agent.local_disk_monitor import LocalDiskMonitor
from backend.agent.log_archiver import (
    LogArchiver,
    collect_archive_heartbeat_metrics,
)
from backend.agent.registry.local_db import LocalDB


@pytest.fixture
def db(tmp_path):
    d = LocalDB()
    d.initialize(str(tmp_path / "agent.db"))
    yield d
    d.close()


@pytest.fixture(autouse=True)
def reset_singletons():
    LogArchiver._reset_for_tests()
    LocalDiskMonitor._reset_for_tests()
    yield
    LogArchiver._reset_for_tests()
    LocalDiskMonitor._reset_for_tests()


def _ok_session():
    sess = MagicMock()
    resp = MagicMock()
    resp.status_code = 200
    sess.post.return_value = resp
    return sess


def _bad_session(status: int = 500):
    sess = MagicMock()
    resp = MagicMock()
    resp.status_code = status
    resp.text = "err"
    sess.post.return_value = resp
    return sess


def _configure_archiver(db, tmp_path, *, session=None, grace=0.0) -> LogArchiver:
    run_log_dir = tmp_path / "logs" / "runs"
    run_log_dir.mkdir(parents=True, exist_ok=True)
    nfs_dir = tmp_path / "nfs"
    nfs_dir.mkdir(exist_ok=True)
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


def _make_job_dir(tmp_path, job_id: int) -> Path:
    job_dir = tmp_path / "logs" / "runs" / str(job_id)
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "init_check.log").write_bytes(b"log line\n")
    past = time.time() - 60  # 回拨 mtime 确保 grace=0 下确定性老化
    os.utime(job_dir, (past, past))
    return job_dir


def test_unconfigured_returns_none():
    """归档子系统未配置 → None（归档禁用 / watcher 未启用时心跳不带 archive 段）。"""
    assert collect_archive_heartbeat_metrics() is None


def test_logarchiver_only_returns_archiver_metrics(db, tmp_path):
    """仅 LogArchiver 配置（LocalDiskMonitor 未配置）→ 仅 archiver 字段，无磁盘字段。"""
    _configure_archiver(db, tmp_path)

    metrics = collect_archive_heartbeat_metrics()

    assert metrics is not None
    assert set(metrics) >= {
        "archived_total",
        "spilled_total",
        "archive_failed",
        "last_archive_at",
        "pending_archive",
    }
    # LocalDiskMonitor 未配置 → 不应混入磁盘字段
    assert "local_disk_usage_pct" not in metrics


def test_both_configured_merges_disk_metrics(db, tmp_path):
    """LogArchiver + LocalDiskMonitor 均配置 → 合并出 §S2.4 要求的完整指标集。"""
    archiver = _configure_archiver(db, tmp_path)
    disk_fn = MagicMock(return_value={"usage_percent": 42.0})
    LocalDiskMonitor.instance().configure(
        archiver=archiver,
        base_dir=str(tmp_path),
        disk_usage_fn=disk_fn,
    ).check_once()  # 记录一次水位 → local_disk_usage_pct=42.0

    metrics = collect_archive_heartbeat_metrics()

    assert metrics is not None
    assert metrics["local_disk_usage_pct"] == 42.0
    assert "spill_threshold_pct" in metrics
    # archiver 与磁盘指标同时在场
    assert "pending_archive" in metrics
    assert "archived_total" in metrics


def test_pending_archive_reflects_cache_after_scan(db, tmp_path):
    """Task1↔Task2 集成：注册失败的已完成 job 保留本地，scan_once 刷新缓存后，
    聚合结果的 pending_archive 体现该积压（且 archive_failed 计数同步）。"""
    _configure_archiver(db, tmp_path, session=_bad_session(503))
    _make_job_dir(tmp_path, 4242)

    archived = LogArchiver.instance().scan_once()
    assert archived == 0  # 注册失败 → 未归档

    metrics = collect_archive_heartbeat_metrics()
    assert metrics is not None
    assert metrics["pending_archive"] == 1
    assert metrics["archive_failed"] >= 1


__all__: list = []
