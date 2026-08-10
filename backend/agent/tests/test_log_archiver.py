"""LogArchiver 单元测试（ADR-0025 方案 C — SSD prune only）。

覆盖：prune happy path / 跳过活跃 job / 跳过未过 grace / grace=0 受 MIN_GRACE 下限。
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from backend.agent.log_archiver import MIN_GRACE_SECONDS, LogArchiver
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


@pytest.fixture(autouse=True)
def reset_singleton():
    LogArchiver._reset_for_tests()
    yield
    LogArchiver._reset_for_tests()


def _configure(db, run_log_dir, *, grace=MIN_GRACE_SECONDS) -> LogArchiver:
    return LogArchiver.instance().configure(
        local_db=db,
        run_log_dir=str(run_log_dir),
        grace_seconds=grace,
    )


def _make_job_dir(
    run_log_dir: Path,
    job_id: int,
    *,
    content: bytes = b"log line\n",
    age_seconds: float = MIN_GRACE_SECONDS + 60,
) -> Path:
    job_dir = run_log_dir / str(job_id)
    job_dir.mkdir()
    (job_dir / "init_check.log").write_bytes(content)
    past = time.time() - age_seconds
    os.utime(job_dir, (past, past))
    return job_dir


def test_prune_happy_path(db, run_log_dir):
    arch = _configure(db, run_log_dir)
    job_dir = _make_job_dir(run_log_dir, 1001)

    n = arch.scan_once()

    assert n == 1
    assert not job_dir.exists()
    assert arch.snapshot_metrics()["pruned_total"] == 1


def test_skip_active_job(db, run_log_dir):
    arch = _configure(db, run_log_dir)
    _make_job_dir(run_log_dir, 2002)
    db.save_active_job(2002, device_id=20, fencing_token="20:1")

    n = arch.scan_once()

    assert n == 0
    assert (run_log_dir / "2002").exists()


def test_skip_not_aged(db, run_log_dir):
    arch = _configure(db, run_log_dir, grace=3600.0)
    _make_job_dir(run_log_dir, 3003)

    n = arch.scan_once()

    assert n == 0
    assert (run_log_dir / "3003").exists()


def test_grace_zero_does_not_prune_young_inactive_dir(db, run_log_dir):
    """P4-2: active_ids 漏记刚启动 Job 时，grace=0 也不能立刻删目录。"""
    arch = _configure(db, run_log_dir, grace=1800.0)
    job_dir = _make_job_dir(run_log_dir, 9001, age_seconds=30)

    n = arch.scan_once(grace_seconds=0.0)

    assert n == 0
    assert job_dir.exists()


def test_grace_zero_prunes_when_past_min_grace(db, run_log_dir):
    arch = _configure(db, run_log_dir, grace=1800.0)
    job_dir = _make_job_dir(run_log_dir, 9002)

    assert not arch.scan_once()

    n = arch.scan_once(grace_seconds=0.0)
    assert n == 1
    assert not job_dir.exists()


def test_configure_grace_below_min_is_clamped(db, run_log_dir):
    arch = _configure(db, run_log_dir, grace=0.0)
    young = _make_job_dir(run_log_dir, 9003, age_seconds=60)
    aged = _make_job_dir(run_log_dir, 9004)

    n = arch.scan_once()

    assert n == 1
    assert young.exists()
    assert not aged.exists()
