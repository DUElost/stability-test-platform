# -*- coding: utf-8 -*-
"""显式 TEST_DATABASE_URL 的机器护栏（#1300，R15-R01）。

testcontainers 兜底路径天然隔离；显式 ``TEST_DATABASE_URL`` 一旦误指生产库，
conftest ``db_session`` 的全表 ``TRUNCATE ... CASCADE`` 就是生产事故。本模块在
conftest 解析显式地址时做两道闸：

1. **隔离命名**：PostgreSQL 库名必须含 ``test``（不区分大小写，如 ``stp_test``）；
2. **运行时配置比对**：与 ``DATABASE_URL``（应用运行时/生产配置）完全相同 →
   拒绝——把运行时配置复制进 TEST_DATABASE_URL 是最可能的误用姿势。

确需指向非常规命名的库（如共享的临时验证库），设
``STP_ALLOW_UNSAFE_TEST_DATABASE_URL=1`` 显式豁免（记 loud warning）。
"""

from __future__ import annotations

import logging
import os
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)


class UnsafeTestDatabaseUrl(ValueError):
    """TEST_DATABASE_URL 触发生产特征护栏（拒绝指向疑似非测试库）。"""


def _allow_unsafe() -> bool:
    return os.getenv("STP_ALLOW_UNSAFE_TEST_DATABASE_URL", "").strip() in (
        "1", "true", "True", "yes",
    )


def _database_name(url: str) -> str:
    """从 SQLAlchemy URL 提取库名（postgresql* 走 path；无 path 视为空）。"""
    try:
        parsed = urlsplit(url)
    except ValueError:
        return ""
    return parsed.path.lstrip("/").split("?")[0].split("/")[0]


def guard_test_database_url(
    url: str,
    *,
    runtime_database_url: str | None = None,
) -> str:
    """校验显式 TEST_DATABASE_URL；通过则原样返回，触发护栏抛
    :class:`UnsafeTestDatabaseUrl`。

    testcontainers 兜底路径**不经此函数**（容器 URL 由本模块生成，天然隔离）。
    """
    if _allow_unsafe():
        logger.warning(
            "test_db_guard_bypassed url=%s — STP_ALLOW_UNSAFE_TEST_DATABASE_URL=1，"
            "TRUNCATE 将作用于该库，请确认不是生产库",
            url,
        )
        return url

    scheme = urlsplit(url).scheme
    if scheme and not scheme.startswith("postgresql"):
        raise UnsafeTestDatabaseUrl(
            f"TEST_DATABASE_URL must be a PostgreSQL URL (got scheme {scheme!r}); "
            "unset TEST_DATABASE_URL to use the testcontainers default"
        )

    dbname = _database_name(url)
    if "test" not in dbname.lower():
        raise UnsafeTestDatabaseUrl(
            f"TEST_DATABASE_URL database name must contain 'test' "
            f"(isolated-naming convention), got dbname={dbname!r}; "
            "unset TEST_DATABASE_URL to use the testcontainers default, or set "
            "STP_ALLOW_UNSAFE_TEST_DATABASE_URL=1 to override"
        )

    runtime = runtime_database_url or ""
    if runtime and url == runtime:
        raise UnsafeTestDatabaseUrl(
            "TEST_DATABASE_URL is identical to DATABASE_URL (runtime config) — "
            "refusing to TRUNCATE the application database"
        )
    return url
