"""#703：在真实 PostgreSQL 上证明 scheduler leadership 的两条前提。

顺序判据在离线侧由 `tests/test_leader_election.py` 钉；本文件证的是**语义前提**——
mock 看不见 psycopg / QueuePool 的归还行为，而 #703 的整条因果链正压在这上面：

1. 取锁那个事务不得跨 job 体：持锁连接在 job 体期间是 ``idle``，不是
   ``idle in transaction``（后者 = 2026-09-13 生产事故里排着 85 个等待会话的根会话形态）；
2. 结束事务的同时锁仍归本上下文：第二个会话取同一 key 必须失败，退出上下文后该 key
   上不再有锁。若改成「``SessionLocal`` + 取锁后 commit」，commit 会把连接连同
   **session 级锁**一起归还池（锁不随 commit/rollback 释放），于是本上下文后面的 unlock
   打在另一条后端上返回 false，锁永久留在池里某条连接上——现象是「该 job 从此再也没有
   leader」，且不会报任何错。这正是本文件要挡住的形态。
3. （#703 残留①）unlock 失败时**作废**那条持锁连接：离线档只能钉「invalidate 在
   close 之前」的形状，「锁随其后端进程一起消失」只能在真 PG 上证。注意判据不是
   巧合变绿的：生产/测试引擎都预置了 ``pool_pre_ping`` + ``pool_recycle=1800``
   （``backend/core/database.py``），旧实现把仍持锁的连接原样回池后，ping 不会丢锁、
   recycle 也远晚于用例时长——`pg_locks` 上会一直挂着这个 holder。
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import text

from backend.core import database as db_mod
from backend.core.leader_election import advisory_lock_key, hold_scheduler_leadership

_PG_ONLY = pytest.mark.skipif(
    os.getenv("DATABASE_URL", "").startswith("sqlite"),
    reason="advisory lock 与 pg_stat_activity 只在 PostgreSQL 上存在",
)

_JOB = "test_leader_election_txn_703"

# advisory 的 64 位 key 拆在 classid/objid 两列里（各 32 位）。
_HOLDERS_SQL = (
    "SELECT a.pid, a.state FROM pg_locks l "
    "JOIN pg_stat_activity a ON a.pid = l.pid "
    "WHERE l.locktype = 'advisory' "
    "AND ((l.classid::bigint << 32) | l.objid::bigint) = :k"
)


def _holders(probe) -> list[tuple[int, str]]:
    return [(r[0], r[1]) for r in probe.execute(text(_HOLDERS_SQL), {"k": advisory_lock_key(_JOB)}).all()]


@_PG_ONLY
def test_body_runs_without_an_open_transaction_but_still_holds_the_lock(monkeypatch):
    """#703 主判据：commit 掉取锁事务之后，锁仍然握在我们这条连接上。"""
    monkeypatch.setenv("TESTING", "0")  # 关掉「测试环境不依赖真锁」的豁免
    key = advisory_lock_key(_JOB)
    with hold_scheduler_leadership(_JOB) as leader:
        assert leader is True
        # 关键一步：job 体期间**别的 worker 从池里取走一条连接**。用 Session 的实现
        # 在这里就把我们那条（连同锁）发了出去；用 ``Connection`` 的实现里这条连接被
        # 本上下文独占，池怎么发都发不到它。
        hog = db_mod.engine.connect()
        # 本上下文独占着一条连接，下面两条 connect() 只可能是**另一个**后端
        with db_mod.engine.connect() as probe:
            held = _holders(probe)
            assert len(held) == 1, f"期望恰好一条持锁连接，实得 {held}"
            _pid, state = held[0]
            assert state != "idle in transaction", (
                "取锁事务跨过了 job 体：该连接停在 idle in transaction（#703 的根会话形态）"
            )
            acquired_by_other = probe.execute(
                text("SELECT pg_try_advisory_lock(:k)"), {"k": key}
            ).scalar()
            if acquired_by_other:  # 互斥失效——锁已不在我们手上，先清干净再红
                probe.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})
                probe.commit()
            assert not acquired_by_other, (
                "第二个会话竟能取到同一 key：commit 把连接（连同 session 级锁）归还了池"
            )
        hog.close()
    with db_mod.engine.connect() as probe:
        assert _holders(probe) == [], "退出上下文后锁未释放——留在池里某条连接上"
        probe.execute(text("SELECT pg_advisory_unlock_all()"))


@_PG_ONLY
def test_unlock_failure_discards_the_lock_holding_connection(monkeypatch):
    """#703 残留①（真 PG 判据）：释锁失败不得把锁带回池。

    注入**一次性**的 unlock 异常（后端连接保持存活、锁仍归它持有）——只有这个
    形态能模拟「回池即把锁带走」。退出上下文后：

    - 旧实现（裸 close）：持锁连接原样回到 QueuePool，`pg_locks` 上该 key 恰有
      一个 holder → 本用例红；
    - 新实现（invalidate → close）：后端被丢弃，锁随其后端进程消失，`pg_locks`
      干净，同 key 在别的会话上可重新取得。
    """
    monkeypatch.setenv("TESTING", "0")  # 关掉「测试环境不依赖真锁」的豁免
    key = advisory_lock_key(_JOB)

    from sqlalchemy.engine import Connection as SAConnection

    real_execute = SAConnection.execute
    boom = {"armed": True}

    def _flaky_execute(self_, statement, *args, **kwargs):
        # 只炸上下文退出时的那一次 unlock；取锁（pg_try_advisory_lock）与其余
        # 语句原样放行。一次性：退出之后探针还要正常干活。
        if boom["armed"] and "SELECT pg_advisory_unlock" in str(statement):
            boom["armed"] = False
            raise RuntimeError("simulated unlock failure (backend still alive)")
        return real_execute(self_, statement, *args, **kwargs)

    monkeypatch.setattr(SAConnection, "execute", _flaky_execute)
    with hold_scheduler_leadership(_JOB) as leader:
        assert leader is True
    # 退出时 unlock 已炸过一次（boom 解除），恢复 execute 让探针可用
    monkeypatch.undo()

    assert boom["armed"] is False, "unlock 故障未触发——本用例没测到失败路径"
    with db_mod.engine.connect() as probe:
        assert _holders(probe) == [], (
            "释锁失败的连接带着 session 级锁回了池——"
            "此后所有副本对该 key 永不可取锁（#703 残留① 的原形态）"
        )
        acquired = probe.execute(
            text("SELECT pg_try_advisory_lock(:k)"), {"k": key}
        ).scalar()
        assert acquired is True, "同 key 重取失败：锁仍挂在池里某条后端上"
        probe.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})
        probe.commit()
