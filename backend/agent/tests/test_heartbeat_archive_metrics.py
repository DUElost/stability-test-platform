"""心跳归档指标聚合测试（ADR-0025 方案 C — LogArchiver SSD prune only）。"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

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
    yield
    LogArchiver._reset_for_tests()


def test_unconfigured_returns_none():
    assert collect_archive_heartbeat_metrics() is None


def test_configured_returns_pruned_total(db, tmp_path):
    run_log_dir = tmp_path / "logs" / "runs"
    run_log_dir.mkdir(parents=True, exist_ok=True)
    LogArchiver.instance().configure(
        local_db=db,
        run_log_dir=str(run_log_dir),
    )

    metrics = collect_archive_heartbeat_metrics()

    assert metrics is not None
    assert "pruned_total" in metrics
