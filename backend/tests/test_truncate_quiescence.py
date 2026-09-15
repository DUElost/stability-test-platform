"""#2074 — 清库静默化：TRUNCATE 前排空共享后台池 + 死锁现场取证，无重试。

#1273 的「DeadlockDetected 3 次退避重试」是过渡止血：泄漏方是 fire-and-forget
后台任务（通知 SAQ 降级直达、post_completion 缓存刷新，各自自开
``SessionLocal`` 短事务）横跨到下一个用例，与清库的 AccessExclusiveLock 成环。
根因收敛 = ``_truncate_all_tables`` 先 ``thread_pool.drain()``；死锁一旦再现，
``_dump_deadlock_scene`` 把锁环两侧落到表级（pid/relname/query），不再只有
relation oid。

这里用假 engine 确定性地验证清库路径（真库复现依赖并发时序，不做 flaky 测试）；
drain 语义用真实线程池 + Event 精确编排（不依赖 wall-clock 时长）。
"""
from __future__ import annotations

import threading

import pytest
from sqlalchemy.exc import OperationalError

from backend.core import thread_pool
from backend.tests.conftest import _dump_deadlock_scene, _truncate_all_tables


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


def test_truncate_drains_background_pool_before_truncate(monkeypatch):
    """清库必须先等后台池静默：drain 在第一条 TRUNCATE 语句之前调用。"""
    order: list[str] = []
    monkeypatch.setattr(
        thread_pool, "drain", lambda timeout=10.0: order.append("drain") or True,
    )

    engine = _FakeEngine(fail_times=0)

    orig_exec = engine.conn.exec_driver_sql

    def _record_exec(_sql: str, _params=None):
        order.append("truncate")
        return orig_exec(_sql, _params)

    engine.conn.exec_driver_sql = _record_exec
    _truncate_all_tables(engine, '"script"')
    assert order == ["drain", "truncate"], (
        f"清库前必须排空后台池，实际顺序 {order}"
    )


def test_deadlock_raises_immediately_and_dumps_scene(monkeypatch):
    """死锁不再重试：一次失败即上抛，但现场取证恰好执行一次。"""
    dumps: list[_FakeEngine] = []
    monkeypatch.setattr(
        "backend.tests.conftest._dump_deadlock_scene",
        lambda engine: dumps.append(engine),
    )
    engine = _FakeEngine(fail_times=99)
    with pytest.raises(OperationalError):
        _truncate_all_tables(engine, '"script"')
    assert engine.conn.calls == 1, "重试已随根因收敛移除，死锁必须一次上抛"
    assert dumps == [engine]


def test_non_deadlock_error_raises_without_dump(monkeypatch):
    engine = _FakeEngine(
        fail_times=1, message="psycopg.errors.UndefinedTable: no such table",
    )
    dumps: list[_FakeEngine] = []
    monkeypatch.setattr(
        "backend.tests.conftest._dump_deadlock_scene",
        lambda engine: dumps.append(engine),
    )
    with pytest.raises(OperationalError):
        _truncate_all_tables(engine, '"script"')
    assert engine.conn.calls == 1
    assert dumps == [], "非死锁错误不走取证"


def test_drain_waits_for_inflight_tasks():
    """drain 的真实语义：在途任务清零前不返回；清零后返回 True。"""
    release = threading.Event()
    started = threading.Event()

    def _task():
        started.set()
        release.wait(timeout=10)

    future = thread_pool.submit(_task)
    try:
        assert started.wait(timeout=10), "任务未开始执行"
        assert thread_pool.queue_depth() >= 1
        assert thread_pool.drain(timeout=0.1) is False, (
            "在途任务未结束时 drain 不得谎报排空"
        )
    finally:
        release.set()
        future.result(timeout=10)
    assert thread_pool.drain(timeout=10) is True


def test_dump_deadlock_scene_never_masks_original_error():
    """取证自身的失败不得外溢：诊断抛错时静默记日志（caplog 可见）。"""
    class _BoomEngine:
        def connect(self):
            raise RuntimeError("diagnostic exploded")

    # 不抛即通过；原异常由调用方（_truncate_all_tables）另行 raise。
    _dump_deadlock_scene(_BoomEngine())
