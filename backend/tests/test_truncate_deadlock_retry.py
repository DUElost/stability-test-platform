"""#1273 — db_session 清库（全表 TRUNCATE）对 DeadlockDetected 的有界重试。

全量套件偶发 7–8 个 setup ERROR：TRUNCATE 取 AccessExclusiveLock 时与同进程内
存活连接/后台线程形成循环等待，PG 把 TRUNCATE 判为牺牲者。死锁是瞬态，重试即可
成功；这里用假 engine 确定性地验证重试路径（真库复现依赖并发时序，不做 flaky 测试）。
"""
from __future__ import annotations

from sqlalchemy.exc import OperationalError

import pytest

from backend.tests.conftest import _truncate_all_tables


class _FakeDialect:
    name = "postgresql"


class _FakeConn:
    def __init__(self, fail_times: int, message: str):
        self.fail_times = fail_times
        self.message = message
        self.calls = 0
        self.dialect = _FakeDialect()

    def exec_driver_sql(self, _sql: str, _params=None):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise OperationalError(
                "TRUNCATE TABLE x", {}, Exception(self.message),
            )
        return None


class _FakeBegin:
    def __init__(self, conn: _FakeConn):
        self._conn = conn

    def __enter__(self) -> _FakeConn:
        return self._conn

    def __exit__(self, *_exc) -> bool:
        return False


class _FakeEngine:
    def __init__(self, fail_times: int, message: str = "psycopg.errors.DeadlockDetected: deadlock detected"):
        self.conn = _FakeConn(fail_times, message)

    def begin(self) -> _FakeBegin:
        return _FakeBegin(self.conn)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr("backend.tests.conftest.time.sleep", lambda _s: None)


def test_retries_truncate_on_deadlock_then_succeeds():
    engine = _FakeEngine(fail_times=2)
    _truncate_all_tables(engine, '"script"')
    assert engine.conn.calls == 3


def test_deadlock_retry_exhaustion_reraises():
    engine = _FakeEngine(fail_times=99)
    with pytest.raises(OperationalError):
        _truncate_all_tables(engine, '"script"')
    assert engine.conn.calls == 4  # 首次 + 3 次重试


def test_non_deadlock_error_is_not_retried():
    engine = _FakeEngine(fail_times=1, message="psycopg.errors.UndefinedTable: no such table")
    with pytest.raises(OperationalError):
        _truncate_all_tables(engine, '"script"')
    assert engine.conn.calls == 1
