"""#2632：PG 连接带 application_name —— 让 PG 日志能归因到「谁连的」。

背景：2026-09-16 起约 30 条「猜出来的 schema」的一次性 SQL 错误（含超级用户）事后只能
人读 `postgresql-*.log`，而日志只有 `user@db`，无法回答来源。控制面与测试各报一个名字后，
每条连接/慢查询都能对上是哪条链路。
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text

from backend.core.database import (
    db_application_name,
    get_async_engine_kwargs,
    get_sync_engine_kwargs,
    is_sqlite_url,
)

_PG_URL = "postgresql+psycopg://u:p@127.0.0.1:5432/stp_test"
_PG_ASYNC_URL = "postgresql+asyncpg://u:p@127.0.0.1:5432/stp_test"


def test_sync_pg_kwargs_carry_application_name():
    args = get_sync_engine_kwargs(_PG_URL)["connect_args"]
    assert isinstance(args, dict) and args["application_name"]


def test_async_pg_kwargs_carry_application_name_via_server_settings():
    """asyncpg 的写法与 psycopg 不同：走 ``server_settings``（实测过的坑）。"""
    args = get_async_engine_kwargs(_PG_ASYNC_URL)["connect_args"]
    assert isinstance(args, dict)
    assert args["server_settings"]["application_name"]


def test_sqlite_kwargs_have_no_connect_args():
    assert "connect_args" not in get_sync_engine_kwargs("sqlite:///tmp/x.db")
    assert "connect_args" not in get_async_engine_kwargs("sqlite+aiosqlite:///tmp/x.db")


def test_name_distinguishes_tests_from_service(monkeypatch):
    monkeypatch.delenv("TESTING", raising=False)
    assert db_application_name() == "stability-backend"
    monkeypatch.setenv("TESTING", "1")
    assert db_application_name() == "stability-tests"


def test_live_connection_reports_the_name(db_session):
    """真连一次读回来——静态断言证明不了 PG 真的收到了。

    用**本模块的 kwargs** 建引擎连同一个（隔离的）库：`db_session` 的引擎由 fixture
    自建、不带这些 kwargs，拿它断言会把「kwargs 没到 PG」误判成"实现没写"。
    """
    # str(URL) 会把口令打码成 ***（重建引擎会认证失败）；本文件只在进程内用，不外泄
    url = db_session.get_bind().url.render_as_string(hide_password=False)
    if is_sqlite_url(url):
        pytest.skip("需要 PostgreSQL")
    engine = create_engine(url, **get_sync_engine_kwargs(url))
    try:
        with engine.connect() as conn:
            got = conn.execute(text("SHOW application_name")).scalar_one()
    finally:
        engine.dispose()
    assert got == db_application_name(), f"PG 侧看到的是 {got!r}"
