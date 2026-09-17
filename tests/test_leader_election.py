"""R01-F10（#890）+ #703：scheduler leadership 的 fail-closed 策略与事务边界。

Postgres 多实例形态下连接 / 取锁失败 → ``yield False``（跳过本轮 tick）；显式禁用 /
``TESTING=1`` / 非 Postgres → fail-open 保留（文档化豁免：单进程形态无双跑）。

#703 追加的判据是**调用顺序**，不是调用次数：取锁之后必须立刻 ``commit()`` 结束那个事务
（否则它被 ``yield`` 挂在整个 job 体上，连接停在 ``idle in transaction``——2026-09-13
生产事故里排着 85 个等待会话的根会话就是这个形态），同时那条 ``Connection`` 必须被本
上下文独占持有到退出（session 级 advisory lock 绑后端进程、不随 commit 释放；连接一旦
归还池，锁就留在池里那条没人认领的连接上）。

顺序为什么用假 ``Connection`` 记录而不是真库：``tests/`` 是离线档（准入判据见
``tests/test_lock_order_pr_path_contract.py`` 头注），真 PG 语义由
``backend/tests/core/test_leader_election_txn.py`` 在 PostgreSQL 上证。
"""

from __future__ import annotations

import os

# backend.core.database 在导入期解析 DATABASE_URL（root tests 无 conftest 注入）；
# 各测试内会用 monkeypatch 覆盖 db_mod.DATABASE_URL 为目标形态。
os.environ.setdefault("DATABASE_URL", "sqlite:///./test-leader-election.db")

from types import SimpleNamespace

import backend.core.database as db_mod
from backend.core.leader_election import hold_scheduler_leadership

_LOCK = "SELECT pg_try_advisory_lock(:k)"
_UNLOCK = "SELECT pg_advisory_unlock(:k)"


def _pg_env(monkeypatch):
    monkeypatch.setenv("TESTING", "0")
    monkeypatch.delenv("STP_SCHEDULER_LEADER_ELECTION", raising=False)
    monkeypatch.setattr(
        db_mod, "DATABASE_URL", "postgresql+psycopg://u:p@db:5432/stp"
    )


class _FakeConn:
    """假 ``Connection``：把 execute / commit / close 记成一条有序调用流。"""

    def __init__(self, *, acquired=True, dialect="postgresql", fail_on=None):
        self.calls: list[tuple[str, str]] = []
        self._acquired = acquired
        self.dialect = SimpleNamespace(name=dialect)
        # fail_on: 命中该 SQL 子串时抛错（"lock" = 取锁阶段，"unlock" = 释放阶段）
        self._fail_on = fail_on

    def execute(self, statement, params=None):
        sql = " ".join(str(statement).split())
        self.calls.append(("execute", sql))
        if self._fail_on and self._fail_on in sql:
            raise RuntimeError("connection reset")
        return SimpleNamespace(scalar=lambda: self._acquired)

    def commit(self):
        self.calls.append(("commit", ""))

    def close(self):
        self.calls.append(("close", ""))

    # 便于断言的可读视图
    @property
    def stages(self) -> list[str]:
        return [
            ("lock" if s == _LOCK else "unlock" if s == _UNLOCK else k)
            for k, s in self.calls
        ]


def _fake_engine(conn=None, connect_raises=None):
    def _connect():
        if connect_raises is not None:
            raise connect_raises
        return conn

    return SimpleNamespace(connect=_connect)


class TestFailClosedOnPostgresFailures:
    def test_connect_failure_skips_tick(self, monkeypatch):
        """#890 故障注入：取不到连接 → yield False（不再 fail-open）。"""
        _pg_env(monkeypatch)
        monkeypatch.setattr(
            db_mod, "engine", _fake_engine(connect_raises=RuntimeError("pool exhausted"))
        )
        with hold_scheduler_leadership("admission_pump") as leader:
            assert leader is False

    def test_lock_acquire_failure_skips_tick(self, monkeypatch):
        """#890 故障注入：pg_try_advisory_lock 抛错 → yield False，且连接被归还。"""
        _pg_env(monkeypatch)
        conn = _FakeConn(fail_on="pg_try_advisory_lock")
        monkeypatch.setattr(db_mod, "engine", _fake_engine(conn))
        with hold_scheduler_leadership("admission_pump") as leader:
            assert leader is False
        assert conn.stages == ["lock", "close"], conn.stages


class TestElectionHappyPaths:
    def test_lock_acquired_yields_true(self, monkeypatch):
        _pg_env(monkeypatch)
        conn = _FakeConn(acquired=True)
        monkeypatch.setattr(db_mod, "engine", _fake_engine(conn))
        with hold_scheduler_leadership("admission_pump") as leader:
            assert leader is True
        # 释放在退出时：unlock + commit，然后 close（唯一的关闭出口）
        assert conn.stages == ["lock", "commit", "unlock", "commit", "close"], conn.stages

    def test_lock_not_acquired_yields_false(self, monkeypatch):
        _pg_env(monkeypatch)
        conn = _FakeConn(acquired=False)
        monkeypatch.setattr(db_mod, "engine", _fake_engine(conn))
        with hold_scheduler_leadership("admission_pump") as leader:
            assert leader is False
        # 没抢到锁就不该有 unlock，也不该有多余的 commit
        assert conn.stages == ["lock", "close"], conn.stages

    def test_unlock_failure_still_closes(self, monkeypatch):
        """释锁失败只记 debug——但连接必须归还，否则每轮 tick 漏一条连接。"""
        _pg_env(monkeypatch)
        conn = _FakeConn(fail_on="pg_advisory_unlock")
        monkeypatch.setattr(db_mod, "engine", _fake_engine(conn))
        with hold_scheduler_leadership("admission_pump") as leader:
            assert leader is True
        assert conn.stages[-1] == "close", conn.stages


class TestTransactionDoesNotOutliveTheAcquire:
    """#703：事务边界与连接归属。两条都是「静默降级」型，只能钉顺序。"""

    def test_acquire_transaction_is_closed_before_the_body_runs(self, monkeypatch):
        _pg_env(monkeypatch)
        conn = _FakeConn(acquired=True)
        monkeypatch.setattr(db_mod, "engine", _fake_engine(conn))
        with hold_scheduler_leadership("admission_pump") as leader:
            assert leader is True
            # 进入 job 体时，取锁那个事务必须已经 commit 掉；否则整轮 body 期间
            # 这条连接都是 idle in transaction（旧实现正是此处红）。
            assert conn.stages == ["lock", "commit"], conn.stages

    def test_connection_is_ours_until_exit(self, monkeypatch):
        """锁是 session 级的：commit 之后、退出之前都不许把连接还给池。"""
        _pg_env(monkeypatch)
        conn = _FakeConn(acquired=True)
        monkeypatch.setattr(db_mod, "engine", _fake_engine(conn))
        with hold_scheduler_leadership("admission_pump"):
            assert "close" not in conn.stages, conn.stages
        assert conn.stages.count("close") == 1, conn.stages

    def test_dialect_probe_failure_path_closes(self, monkeypatch):
        """非 Postgres 方言兜底放行时也必须归还连接（旧分支只靠 GC 兜）。"""
        _pg_env(monkeypatch)
        conn = _FakeConn(dialect="sqlite")
        monkeypatch.setattr(db_mod, "engine", _fake_engine(conn))
        with hold_scheduler_leadership("admission_pump") as leader:
            assert leader is True
        assert conn.stages == ["close"], conn.stages


class TestDocumentedFailOpenExemptions:
    def _never_connect(self, monkeypatch):
        monkeypatch.setattr(
            db_mod, "engine",
            _fake_engine(connect_raises=AssertionError("豁免路径不得触达 DB")),
        )

    def test_disabled_election_is_fail_open_by_design(self, monkeypatch):
        """显式退出协调（遗留单进程）→ 恒 leader——fail-open 仅存于此豁免路径。"""
        monkeypatch.setenv("TESTING", "0")
        monkeypatch.setenv("STP_SCHEDULER_LEADER_ELECTION", "0")
        monkeypatch.setattr(db_mod, "DATABASE_URL", "postgresql+psycopg://u:p@db:5432/stp")
        self._never_connect(monkeypatch)
        with hold_scheduler_leadership("admission_pump") as leader:
            assert leader is True

    def test_testing_env_yields_true(self, monkeypatch):
        monkeypatch.setenv("TESTING", "1")
        monkeypatch.delenv("STP_SCHEDULER_LEADER_ELECTION", raising=False)
        self._never_connect(monkeypatch)
        with hold_scheduler_leadership("admission_pump") as leader:
            assert leader is True

    def test_non_postgres_yields_true(self, monkeypatch):
        monkeypatch.setenv("TESTING", "0")
        monkeypatch.delenv("STP_SCHEDULER_LEADER_ELECTION", raising=False)
        monkeypatch.setattr(db_mod, "DATABASE_URL", "sqlite:///./local.db")
        self._never_connect(monkeypatch)
        with hold_scheduler_leadership("admission_pump") as leader:
            assert leader is True
