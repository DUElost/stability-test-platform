"""Control-plane scheduler leadership (ADR-0027 P3-1；fail-closed 策略见修订)。

Singleton APScheduler jobs (admission pump, counter reconcile, …) must run on
at most one control-plane process. Under the historical single-process
constraint this was implicit; once multi-instance is allowed, each tick
acquires a Postgres session-level advisory lock for the job name.

Behaviour:
- ``STP_SCHEDULER_LEADER_ELECTION=0`` → always leader（显式退出协调——遗留
  单进程模式，单进程即无双跑；fail-open 仅存于此显式豁免路径）。
- ``=1`` (default) → ``pg_try_advisory_lock``；SQLite / ``TESTING=1`` /
  non-Postgres → always leader（非多实例部署形态，文档化豁免）。
- **Postgres + 连接/取锁失败 → fail-closed（跳过本轮 tick，#890）**：DB 不可用
  期间 singleton job 本就依赖同一 DB，跳过无可用性损失；多实例下 fail-open
  会让所有副本同时自认 leader，双跑风险不对称地大于跳过成本。DB 恢复后
  tick 自动恢复。
- Lock is held only for the duration of the ``leadership`` context and is
  released on exit (or when the holding connection closes). The context owns
  one checked-out ``Connection`` for its whole life: the lock is
  session/backend-scoped, so the connection must not go back to the pool
  while we still hold it (#703).
- **The acquire transaction never outlives the acquire** (#703): right after
  the lock is taken the transaction is committed, so a singleton job body
  (minutes long) does not park its connection in ``idle in transaction``.
  A session-level advisory lock survives that ``commit()``.
"""

from __future__ import annotations

import hashlib
import logging
import os
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import text

logger = logging.getLogger(__name__)

_FALSEY = frozenset({"0", "false", "False", "no", "NO", "off", "OFF"})


def leader_election_enabled() -> bool:
    """Default ON: mistaken multi-instance deploys still serialise singleton jobs."""
    return os.getenv("STP_SCHEDULER_LEADER_ELECTION", "1").strip() not in _FALSEY


def advisory_lock_key(job_name: str) -> int:
    """Stable signed 63-bit key derived from job name (Postgres bigint)."""
    digest = hashlib.sha256(f"stp:scheduler:leader:{job_name}".encode()).digest()
    return int.from_bytes(digest[:8], "big") & 0x7FFFFFFFFFFFFFFF


@contextmanager
def hold_scheduler_leadership(job_name: str) -> Iterator[bool]:
    """Yield True iff this process should run ``job_name`` this tick.

    When election is disabled, always yields True without touching the DB.
    """
    if not leader_election_enabled():
        yield True
        return

    # Pytest / agent suites must not depend on a live Postgres advisory lock.
    if os.getenv("TESTING") == "1":
        yield True
        return

    from backend.core.database import is_sqlite_url, normalize_sync_database_url
    import backend.core.database as db_mod

    sync_url = normalize_sync_database_url(db_mod.DATABASE_URL)
    if is_sqlite_url(sync_url):
        yield True
        return
    if not sync_url.startswith("postgresql"):
        yield True
        return

    key = advisory_lock_key(job_name)
    # #703：**这里刻意用 Connection 而不是 Session**。session 级 advisory lock 绑在
    # 后端进程上，commit / rollback 都不释放它；而 ``Session.commit()`` 会把连接**归还
    # 池**——锁就留在池里那条再没人认领的连接上（同 key 永不释放，别的副本从此当不了
    # leader）。``Connection`` 在 ``close()`` 前独占这条连接，于是可以在「保住锁」的
    # 同时结束事务（见下方取锁后的 commit）。
    try:
        conn = db_mod.engine.connect()
    except Exception:
        # R01-F10（#890）：真 Postgres 多实例形态下失败不再 fail-open——
        # 所有副本同时自认 leader 的双跑风险不对称地大于跳过一轮 tick。
        logger.warning(
            "scheduler_leadership_fail_closed job=%s reason=connect "
            "(skipping tick)",
            job_name,
            exc_info=True,
        )
        yield False
        return

    acquired = False
    try:
        try:
            # Probe dialect without assuming connect succeeded until execute.
            if conn.dialect.name != "postgresql":
                yield True
                return
            acquired = bool(
                conn.execute(
                    text("SELECT pg_try_advisory_lock(:k)"),
                    {"k": key},
                ).scalar()
            )
        except Exception:
            logger.warning(
                "scheduler_leadership_fail_closed job=%s reason=lock_acquire "
                "(skipping tick)",
                job_name,
                exc_info=True,
            )
            yield False
            return

        if not acquired:
            logger.debug("scheduler_leadership_skipped job=%s", job_name)
            yield False
            return

        # #703：取锁事务**就地结束**。不 commit 的话，这个事务被 ``yield`` 挂在整个
        # job 体上（singleton job 分钟级），连接一直停在 ``idle in transaction``——
        # 2026-09-13 生产事故里那条根会话的形态正是
        # ``state=idle in transaction`` + ``SELECT pg_try_advisory_lock($1)``，
        # 其后排着 85 个等它的会话（当时 app 池之外还顶满了 PG max_connections）。
        # 互斥性不受影响：session 级锁不随 commit 释放。
        conn.commit()
        yield True
    finally:
        if acquired:
            try:
                conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})
                conn.commit()
            except Exception:
                logger.debug(
                    "scheduler_leadership_unlock_failed job=%s",
                    job_name,
                    exc_info=True,
                )
        try:
            # 唯一的关闭出口：取锁失败、未抢到、非 Postgres 方言提前放行，都走这里
            # 归还连接（旧实现在方言分支上不关闭，只靠 GC 兜）。
            conn.close()
        except Exception:
            pass
