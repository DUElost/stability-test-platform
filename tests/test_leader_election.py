"""R01-F10（#890）：scheduler leadership fail-closed 策略（故障注入测试）。

Postgres 多实例形态下 session 工厂 / 取锁失败 → ``yield False``（跳过本轮
tick）；显式禁用 / ``TESTING=1`` / 非 Postgres → fail-open 保留（文档化豁免：
单进程形态无双跑）。
"""

from __future__ import annotations

import os

# backend.core.database 在导入期解析 DATABASE_URL（root tests 无 conftest 注入）；
# 各测试内会用 monkeypatch 覆盖 db_mod.DATABASE_URL 为目标形态。
os.environ.setdefault("DATABASE_URL", "sqlite:///./test-leader-election.db")

from types import SimpleNamespace
from unittest.mock import MagicMock

import backend.core.database as db_mod
from backend.core.leader_election import hold_scheduler_leadership


def _pg_env(monkeypatch):
    monkeypatch.setenv("TESTING", "0")
    monkeypatch.delenv("STP_SCHEDULER_LEADER_ELECTION", raising=False)
    monkeypatch.setattr(
        db_mod, "DATABASE_URL", "postgresql+psycopg://u:p@db:5432/stp"
    )


def _fake_db(execute_raises: Exception | None = None, acquired: bool = True):
    dialect = SimpleNamespace(name="postgresql")
    if execute_raises is not None:
        execute = MagicMock(side_effect=execute_raises)
    else:
        result = MagicMock()
        result.scalar.return_value = acquired
        execute = MagicMock(return_value=result)
    db = MagicMock()
    db.get_bind.return_value = SimpleNamespace(dialect=dialect)
    db.execute = execute
    return db


class TestFailClosedOnPostgresFailures:
    def test_session_factory_failure_skips_tick(self, monkeypatch):
        """#890 故障注入：SessionLocal() 抛错 → yield False（不再 fail-open）。"""
        _pg_env(monkeypatch)

        def _boom():
            raise RuntimeError("pool exhausted")

        monkeypatch.setattr(db_mod, "SessionLocal", _boom)
        with hold_scheduler_leadership("admission_pump") as leader:
            assert leader is False

    def test_lock_acquire_failure_skips_tick(self, monkeypatch):
        """#890 故障注入：pg_try_advisory_lock 抛错 → yield False。"""
        _pg_env(monkeypatch)
        db = _fake_db(execute_raises=RuntimeError("connection reset"))
        monkeypatch.setattr(db_mod, "SessionLocal", lambda: db)
        with hold_scheduler_leadership("admission_pump") as leader:
            assert leader is False
        db.close.assert_called_once()


class TestElectionHappyPaths:
    def test_lock_acquired_yields_true(self, monkeypatch):
        _pg_env(monkeypatch)
        db = _fake_db(acquired=True)
        monkeypatch.setattr(db_mod, "SessionLocal", lambda: db)
        with hold_scheduler_leadership("admission_pump") as leader:
            assert leader is True
        # 释放在 finally：unlock + commit 都发生
        assert db.execute.call_count == 2
        db.commit.assert_called_once()

    def test_lock_not_acquired_yields_false(self, monkeypatch):
        _pg_env(monkeypatch)
        db = _fake_db(acquired=False)
        monkeypatch.setattr(db_mod, "SessionLocal", lambda: db)
        with hold_scheduler_leadership("admission_pump") as leader:
            assert leader is False
        db.close.assert_called_once()


class TestDocumentedFailOpenExemptions:
    def test_disabled_election_is_fail_open_by_design(self, monkeypatch):
        """显式退出协调（遗留单进程）→ 恒 leader——fail-open 仅存于此豁免路径。"""
        monkeypatch.setenv("TESTING", "0")
        monkeypatch.setenv("STP_SCHEDULER_LEADER_ELECTION", "0")
        monkeypatch.setattr(db_mod, "DATABASE_URL", "postgresql+psycopg://u:p@db:5432/stp")

        def _boom():
            raise AssertionError("禁用选举时不得触达 DB")

        monkeypatch.setattr(db_mod, "SessionLocal", _boom)
        with hold_scheduler_leadership("admission_pump") as leader:
            assert leader is True

    def test_testing_env_yields_true(self, monkeypatch):
        monkeypatch.setenv("TESTING", "1")
        monkeypatch.delenv("STP_SCHEDULER_LEADER_ELECTION", raising=False)

        def _boom():
            raise AssertionError("TESTING=1 不得触达 DB")

        monkeypatch.setattr(db_mod, "SessionLocal", _boom)
        with hold_scheduler_leadership("admission_pump") as leader:
            assert leader is True

    def test_non_postgres_yields_true(self, monkeypatch):
        monkeypatch.setenv("TESTING", "0")
        monkeypatch.delenv("STP_SCHEDULER_LEADER_ELECTION", raising=False)
        monkeypatch.setattr(db_mod, "DATABASE_URL", "sqlite:///./local.db")

        def _boom():
            raise AssertionError("SQLite 形态不得触达 DB")

        monkeypatch.setattr(db_mod, "SessionLocal", _boom)
        with hold_scheduler_leadership("admission_pump") as leader:
            assert leader is True
