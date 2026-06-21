"""LogArchiver 单元测试（ADR-0025 方案 C — SSD prune only）。

覆盖：prune happy path / 跳过活跃 job / 跳过未过 grace / grace=0 立即 prune。
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from backend.agent.log_archiver import LogArchiver
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


def _configure(db, run_log_dir, *, grace=0.0) -> LogArchiver:
    return LogArchiver.instance().configure(
        local_db=db,
        run_log_dir=str(run_log_dir),
        grace_seconds=grace,
    )


def _make_job_dir(run_log_dir: Path, job_id: int, *, content: bytes = b"log line\n") -> Path:
    job_dir = run_log_dir / str(job_id)
    job_dir.mkdir()
    (job_dir / "init_check.log").write_bytes(content)
    past = time.time() - 60
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


def test_grace_zero_prunes_immediately(db, run_log_dir):
    arch = _configure(db, run_log_dir, grace=3600.0)
    _make_job_dir(run_log_dir, 9001)
    assert not arch.scan_once()

    n = arch.scan_once(grace_seconds=0.0)
    assert n == 1
    assert not (run_log_dir / "9001").exists()
