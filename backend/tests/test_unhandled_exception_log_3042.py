"""#3042：一次未捕获异常的**日志体积**必须是契约的一部分，而不是巧合。

现场（本机生产控制面，2026-09-21）：PG 连接槽耗尽实时发生时，`backend.log` 尾部
8 MB / 98,440 行里有 228 条 traceback 记录，**每条 367 行**，约 96% 的字节由
`global_exception_handler` 的一句 `logger.exception` 产生 ⇒ 现场可查时长被压到 1/8。

本文件钉的是**形态**而不是「有没有日志」：

- 数据库侧失败：每种族全栈一次，之后一行且**零 traceback**（`exc_info is None`）；
- 一行里必须留得住定位维度：异常类链、SQLSTATE、端点**模板**、客户端地址；
- 非数据库异常（真 bug）**继续全栈**——收体积不得把诊断能力一起收掉；
- 状态码与响应体形状不变（500 + `INTERNAL_ERROR`），本单只治体量。
"""
from __future__ import annotations

import logging

import asyncpg.exceptions as asyncpg_exc
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import DBAPIError, InvalidRequestError
from sqlalchemy.exc import TimeoutError as SAPoolTimeoutError

from backend.core import exception_log
from backend.main import fastapi_app

_TEST_PATH = "/api/v1/__test_3042__/boom/{item_id}"
_BUG_PATH = "/api/v1/__test_3042__/buggy"


def _db_api_error() -> DBAPIError:
    """按方言的真实翻译路径造一条「槽耗尽」异常（`raise translated from orig`）。"""
    orig = asyncpg_exc.TooManyConnectionsError("remaining connection slots are reserved")
    error = DBAPIError("SELECT 1 FROM plan_run", {}, orig)
    error.__cause__ = orig
    return error


@pytest.fixture
def fresh_families(monkeypatch: pytest.MonkeyPatch):
    """族「是否首次」是**进程内状态**——不隔离就会随测试顺序翻脸。"""
    monkeypatch.setattr(exception_log, "_seen_families", {})
    yield


@pytest.fixture
def app_with_boom():
    """把抛错路由挂到**真实 app** 上（走真实的 exception_handler 注册链）。"""
    def boom_db(item_id: int):
        raise _db_api_error()

    def boom_bug():
        raise ValueError("a genuine programming bug")

    before = len(fastapi_app.router.routes)
    fastapi_app.get(_TEST_PATH)(boom_db)
    fastapi_app.get(_BUG_PATH)(boom_bug)
    added = list(fastapi_app.router.routes[before:])
    try:
        # `Exception` 处理器由 Starlette 的 ServerErrorMiddleware 调用后**仍向上抛**
        # （生产里 uvicorn 不再重复打栈：实测 8 MB 里 225 条只有 1 次栈），
        # 故测试侧必须自己吞掉这次 re-raise 才看得到 500 响应。
        yield TestClient(fastapi_app, raise_server_exceptions=False)
    finally:
        for route in added:
            fastapi_app.router.routes.remove(route)


def _records(caplog):
    return [r for r in caplog.records if r.name == "backend.main"]


def test_db_failure_first_of_family_keeps_one_traceback(app_with_boom, caplog, fresh_families):
    caplog.set_level(logging.ERROR, logger="backend.main")
    resp = app_with_boom.get("/api/v1/__test_3042__/boom/41981")

    assert resp.status_code == 500
    # 响应契约不变：本单只治日志体积，不动状态码与形状
    assert resp.json() == {
        "data": None,
        "error": {"code": "INTERNAL_ERROR", "message": "Internal server error"},
    }
    recs = _records(caplog)
    assert len(recs) == 1, [r.getMessage() for r in recs]
    assert "first_of_family=1" in recs[0].getMessage()
    assert recs[0].exc_info is not None, "该族首次出现必须留一次全栈，否则无从定位根因"


def test_db_failure_repeat_is_one_line_and_keeps_locating_dimensions(
    app_with_boom, caplog, fresh_families
):
    caplog.set_level(logging.ERROR, logger="backend.main")
    for _ in range(3):
        app_with_boom.get("/api/v1/__test_3042__/boom/41981")

    recs = _records(caplog)
    assert len(recs) == 3, [r.getMessage() for r in recs]
    first, later = recs[0], recs[1:]
    assert first.exc_info is not None and "first_of_family=1" in first.getMessage()

    for rec in later:
        assert rec.exc_info is None, "非首次仍打全栈 = #3042 的 367 行回归"
        assert len(rec.getMessage().splitlines()) == 1, "稳态记录必须单行（无内嵌栈）"
    rendered = "\n".join(
        logging.Formatter("%(levelname)s %(name)s %(message)s").format(rec) for rec in later
    )
    assert len(rendered.splitlines()) == len(later), "稳态每条失败必须恰好一行"

    message = later[0].getMessage()
    # 判据 1 的四个维度，缺一个就是「把婴儿倒掉」
    assert "unhandled_db_failure" in message
    assert "TooManyConnectionsError" in message, "根因异常类名必须在"
    assert "sqlstate=53300" in message, "SQLSTATE 必须在——它是分类本身"
    assert f"endpoint={_TEST_PATH}" in message, "端点必须是模板，带 ID 的原始 path 会撑爆键"
    assert "/boom/41981" not in message, "原始 path 不得泄漏进日志"
    assert "method=GET" in message
    assert "client=" in message


def test_non_db_exception_still_gets_full_traceback(app_with_boom, caplog, fresh_families):
    caplog.set_level(logging.ERROR, logger="backend.main")
    resp = app_with_boom.get("/api/v1/__test_3042__/buggy")

    assert resp.status_code == 500
    recs = _records(caplog)
    assert len(recs) == 1
    assert recs[0].exc_info is not None, "真 bug 被降级成一行 = 把诊断能力一起收掉"
    # 旧文案逐字保留（排查习惯不破，同 #2960 的处理）
    assert recs[0].getMessage().startswith("Unhandled exception on GET ")


@pytest.mark.parametrize(
    ("exc", "expected_state"),
    [
        (_db_api_error(), "sqlstate=53300"),
        (SAPoolTimeoutError("QueuePool limit of size 5 reached"), "sqlstate=-"),
        (ValueError("boom"), None),
        (InvalidRequestError("a misuse of the ORM", None, None), None),
    ],
)
def test_family_classification(exc, expected_state):
    fingerprint = exception_log.db_failure_fingerprint(exc)
    if expected_state is None:
        assert fingerprint is None, f"{type(exc).__name__} 不得被当成本地化失败族压栈"
    else:
        assert expected_state in fingerprint
        assert type(exc).__name__ in fingerprint


def test_family_registry_is_bounded(fresh_families):
    """族键无界 = 用一个新的内存事故替换日志事故。"""
    for index in range(exception_log._MAX_TRACKED_FAMILIES + 40):
        exception_log._note_family(f"family-{index}")
    assert len(exception_log._seen_families) <= exception_log._MAX_TRACKED_FAMILIES
    # 让位后重现 → 重新算「首次」，宁可多给一次全栈也不无界缓存
    assert exception_log._note_family("family-0") == 1
