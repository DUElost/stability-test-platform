"""#2104：/metrics 的锁等待 gauge —— 锁序修好后「死锁改等待」的唯一观测面。

锁序修复（#1959/#1980/#1985/#2022）消掉了环路等待，但代价会转移到**普通等待**：
保留清理事务持行锁期间热路径排队、反向亦然。这类等待对
``stability_db_deadlock_total`` 完全不可见，只看死锁计数会得出「计数为 0 = 无代价」
的错误结论（共享行加锁表的 Revisit 已登记）。

判据不是「字段出现」而是**真能看见等待**：用两个额外会话制造一次稳定的行锁排队
（A 持 ``FOR UPDATE``，B 撞同一行），抓 /metrics 断言 ``waiters >= 1`` 且
``max_wait_seconds > 0``；释放后复抓断言回到 0。少了非零分支，「采样恒返回 0」
会以「字段存在」蒙混过关。

需要 PostgreSQL：``pg_stat_activity`` / 行锁都是 PG 专有。
"""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timezone

import pytest
from sqlalchemy import select, text

pytestmark = pytest.mark.skipif(
    os.getenv("DATABASE_URL", "").startswith("sqlite"),
    reason="锁等待观测需要 PostgreSQL（pg_stat_activity / 行锁）",
)

from backend.models.host import Host

_WAITERS = "stability_db_lock_waiters"
_MAX_WAIT = "stability_db_lock_wait_max_seconds"


def _gauge(body: str, name: str) -> float:
    for line in body.splitlines():
        if line.startswith(name + " "):
            return float(line.split(" ", 1)[1])
    raise AssertionError(f"{name} 未出现在 /metrics 输出里")


def _seed_host(db_session) -> str:
    host = Host(
        id="lock-wait-host", hostname="lock-wait-host",
        status="ONLINE", created_at=datetime.now(timezone.utc),
    )
    db_session.add(host)
    db_session.commit()
    return host.id


def _wait_until_this_session_waits(observer, pid_box: list[int], timeout: float = 15.0) -> bool:
    """等到**本用例那个等待会话**真的在等锁。

    判据必须锁定自己的 pid，不能只判「存在任意未获授锁」：进程内的后台泵
    （admission queue pump 等）会有瞬时等待，只判「有等待」会在**我方尚未阻塞**时
    就返回，抓取早于阻塞 → 读到 0 而误判实现有问题（实测踩过）。
    也不能按关系名 / `pg_stat_activity.query` 文本匹配：等一行表现为等对方的
    ``transactionid``，该 `pg_locks` 行的 relation 为 **NULL**，且阻塞会话显示的
    可能是**上一条**语句（#2022 的回归已踩过这两个坑）。
    """
    deadline = time.monotonic() + timeout
    stmt = text("SELECT count(*) FROM pg_locks WHERE NOT granted AND pid = :pid")
    while time.monotonic() < deadline:
        if pid_box and observer.execute(stmt, {"pid": pid_box[0]}).scalar():
            return True
        time.sleep(0.05)
    return False


def test_metrics_exposes_lock_wait_gauges(client, engine, db_session, monkeypatch):
    monkeypatch.setenv("STP_METRICS_AUTH_REQUIRED", "0")
    host_id = _seed_host(db_session)

    # 稳态：无争用
    assert _gauge(client.get("/metrics").text, _WAITERS) == 0.0

    blocker = engine.connect()
    tx = blocker.begin()
    blocker.execute(select(Host.id).where(Host.id == host_id).with_for_update())

    waiter_errors: list[Exception] = []
    waiter_pid: list[int] = []

    def _waiter() -> None:
        conn = engine.connect()
        try:
            waiter_pid.append(
                int(conn.execute(text("SELECT pg_backend_pid()")).scalar_one())
            )
            conn.execute(select(Host.id).where(Host.id == host_id).with_for_update())
        except Exception as exc:  # pragma: no cover - 仅在锁/连接异常时进入
            waiter_errors.append(exc)
        finally:
            conn.rollback()
            conn.close()

    thread = threading.Thread(target=_waiter, daemon=True)
    thread.start()

    observer = engine.connect()
    try:
        assert _wait_until_this_session_waits(observer, waiter_pid), (
            "本用例的等待会话未进入锁等待——本用例失去意义"
        )
        body = client.get("/metrics").text
        waiters = _gauge(body, _WAITERS)
        max_wait = _gauge(body, _MAX_WAIT)
    finally:
        tx.rollback()
        blocker.close()
        thread.join(timeout=30)
        observer.close()

    assert not thread.is_alive(), "释放阻塞后等待方仍未结束"
    assert waiters >= 1, f"确有会话在等锁，{_WAITERS} 却是 {waiters}"
    assert max_wait > 0, f"等待已持续，{_MAX_WAIT} 却是 {max_wait}"
    assert not waiter_errors, waiter_errors

    # 释放后回到稳态（观测面不得把历史等待留成常驻非零）
    assert _gauge(client.get("/metrics").text, _WAITERS) == 0.0


def test_gauge_scope_is_current_database(client, engine, monkeypatch):
    """#2144：等待 gauge 只算**本实例的库**（``datname = current_database()`` 是正确性前提）。

    ``pg_stat_activity`` 是全实例视图，所以那条过滤条件删掉就会把**别的库**的等待算进本
    实例的指标（同一个 PG 集群上跑多个库很常见）。

    护栏做法：在**另一个库**（``postgres``，stock PG 一定有）用 advisory lock 制造一次
    持续数秒的行/锁等待，断言本实例 ``/metrics`` 的 ``max_wait_seconds`` 仍 < 1s。
    不去断言 ``== 0``：本库的 admission pump 等会有亚秒级瞬时等待，那会把护栏变成 flaky。
    用 advisory lock 而不是表行锁，是为了不建表、不锁系统目录（对外零副作用）；它在
    ``wait_event_type='Lock'`` 上与行锁同类，因此能真实检验上面的过滤条件。
    """
    from sqlalchemy import create_engine

    monkeypatch.setenv("STP_METRICS_AUTH_REQUIRED", "0")

    other_db = create_engine(engine.url.set(database="postgres"))
    holder = other_db.connect()
    waiter = other_db.connect()
    waiter_errors: list[Exception] = []
    waiter_pid: list[int] = []
    holder.execute(text("SELECT pg_advisory_lock(918273)"))  # 在别的库持锁

    def _waiter() -> None:
        try:
            waiter_pid.append(
                int(waiter.execute(text("SELECT pg_backend_pid()")).scalar_one())
            )
            waiter.execute(text("SELECT pg_advisory_lock(918273)"))  # 在此阻塞
        except Exception as exc:  # pragma: no cover - 仅在锁/连接异常时进入
            waiter_errors.append(exc)

    thread = threading.Thread(target=_waiter, daemon=True)
    thread.start()

    observer = engine.connect()
    try:
        assert _wait_until_this_session_waits(observer, waiter_pid), (
            "别库的等待会话未进入锁等待——本用例失去意义"
        )
        body = client.get("/metrics").text
        max_wait = _gauge(body, _MAX_WAIT)
        waiters = _gauge(body, _WAITERS)
    finally:
        holder.execute(text("SELECT pg_advisory_unlock(918273)"))
        thread.join(timeout=30)
        holder.close()
        waiter.close()
        observer.close()

    assert not thread.is_alive(), "释放阻塞后等待方仍未结束"
    assert not waiter_errors, waiter_errors
    assert max_wait < 1.0, (
        f"别库的等待被算进了本实例指标（{_MAX_WAIT}={max_wait}s）——"
        "检查 _LOCK_WAIT_SQL 是否还带 `datname = current_database()`"
    )
    assert waiters == 0.0, f"别库的等待不该计入 {_WAITERS}（实际 {waiters}）"
