"""ADR-0047 D2（#2959）：数据库过载族对外契约 = 503 + `Retry-After`，不再是 500。

为什么值得单独一个文件：R523 现场 1530 个 500 里，1053 个是 `/complete`——
Agent 对 500 与 503 一视同仁地**线程内重试 3 次**（1s/2s），把过载尖峰自己续上。
过载与「真 bug」共用一张面孔时，调用方只能无差别重试；分流后：

- 过载（池排队超时 / SQLSTATE 53300）→ 503 + `Retry-After` + `retryable: true`；
- 其余 DB 失败（死锁 `40P01`、连接中断 `08xxx`…）与非 DB 异常 → 500，语义不变。

判定面在 `backend/core/exception_log.py::is_db_overload`；这里钉的是**对外可见**的部分
（状态码、头、体），以及「不该被判成过载的别蹭进来」。
"""

from __future__ import annotations

import asyncpg.exceptions as asyncpg_exc
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.exc import TimeoutError as SAPoolTimeoutError

from backend.core.exception_log import DB_OVERLOAD_RETRY_AFTER_SECONDS, is_db_overload
from backend.core.terminal_bulkhead import TerminalBulkheadFull
from backend.main import fastapi_app

_PATH = "/api/v1/__test_2959__/{kind}"


def _dbapi_from(orig: BaseException) -> DBAPIError:
    error = DBAPIError("SELECT 1 FROM plan_run", {}, orig)
    error.__cause__ = orig
    return error


@pytest.fixture
def app_with_kinds():
    """四种失败各挂一条路由，走真实 exception_handler 注册链。"""

    def _raising(factory):
        def _endpoint():
            raise factory()

        return _endpoint

    raisers = {
        "slots": _raising(
            lambda: _dbapi_from(
                asyncpg_exc.TooManyConnectionsError("remaining connection slots are reserved")
            )
        ),
        "pool_timeout": _raising(
            lambda: SAPoolTimeoutError("QueuePool limit of size 20 overflow 20 reached")
        ),
        "deadlock": _raising(
            lambda: _dbapi_from(asyncpg_exc.DeadlockDetectedError("deadlock detected"))
        ),
        "bug": _raising(lambda: ValueError("a genuine programming bug")),
        "bulkhead": _raising(
            lambda: TerminalBulkheadFull("terminal bulkhead wait budget 500ms exceeded")
        ),
    }

    before = len(fastapi_app.router.routes)
    for kind, raiser in raisers.items():
        fastapi_app.get(_PATH.format(kind=kind))(raiser)
    added = list(fastapi_app.router.routes[before:])
    try:
        # 与 #3042 同形：Exception handler 由 ServerErrorMiddleware 调完仍向上抛，
        # 测试侧要自己吞掉才看得到响应。
        yield TestClient(fastapi_app, raise_server_exceptions=False)
    finally:
        for route in added:
            fastapi_app.router.routes.remove(route)


# ── 判定面（纯函数） ─────────────────────────────────────────────────────────


def test_is_db_overload_accepts_slots_exhausted_and_pool_timeout():
    assert is_db_overload(
        _dbapi_from(asyncpg_exc.TooManyConnectionsError("slots"))
    ) is True
    assert is_db_overload(SAPoolTimeoutError("pool exhausted")) is True


def test_is_db_overload_reads_pgcode_for_psycopg_style_errors():
    """psycopg 系带 `pgcode` 而非 `sqlstate`——两边都要认（部署换驱动不该改语义）。"""

    class _PsycopgLike(OperationalError):
        pgcode = "53300"

    exc = _PsycopgLike("remaining connection slots are reserved", None, None)
    assert is_db_overload(exc) is True


@pytest.mark.parametrize(
    "exc",
    [
        _dbapi_from(asyncpg_exc.DeadlockDetectedError("deadlock detected")),
        ValueError("bug"),
        RuntimeError("other"),
    ],
)
def test_is_db_overload_rejects_other_failures(exc):
    """死锁/连接中断/真 bug 不蹭「过载」：处置不同，不许一起改语义。"""
    assert is_db_overload(exc) is False


# ── 对外契约（真实 handler 链） ──────────────────────────────────────────────


@pytest.mark.parametrize("kind", ["slots", "pool_timeout", "bulkhead"])
def test_overload_returns_503_with_retry_after(app_with_kinds, kind):
    resp = app_with_kinds.get(_PATH.format(kind=kind))

    assert resp.status_code == 503
    assert resp.headers["Retry-After"] == str(DB_OVERLOAD_RETRY_AFTER_SECONDS)
    assert resp.json() == {
        "data": None,
        "error": {
            "code": "DB_OVERLOADED",
            "message": "Database overloaded, retry later",
            "retryable": True,
        },
    }


def test_complete_route_is_wrapped_by_bulkhead():
    """接线守卫：`/complete` 必须在**第一次 DB 查询之前**持有舱壁名额。

    行为面已由 `test_terminal_bulkhead_2959.py` 覆盖（名额/预算/不漏名额），这里钉的是
    「这条路由真的用了它」——舱壁写好了却忘了接线，等于没有（本仓已有的形态）。
    """
    import inspect

    from backend.api.routes import agent_api

    source = inspect.getsource(agent_api.complete_job)
    assert "async with terminal_slot():" in source, "/complete 未接舱壁闸门"
    assert source.index("terminal_slot()") < source.index("complete_agent_job("), (
        "舱壁必须在第一次 DB 调用之前"
    )


@pytest.mark.parametrize("kind", ["deadlock", "bug"])
def test_non_overload_keeps_500_contract(app_with_kinds, kind):
    resp = app_with_kinds.get(_PATH.format(kind=kind))

    assert resp.status_code == 500
    assert "Retry-After" not in resp.headers
    assert resp.json() == {
        "data": None,
        "error": {"code": "INTERNAL_ERROR", "message": "Internal server error"},
    }
