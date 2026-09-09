"""#942：种子迁移治理模板的带数据行为测试（真实 Postgres）。

覆盖裁决 A 的三分支：无引用放行 / 有引用失败（含指引文案）/ 批量停用形态。
表结构用最小建表——模板只依赖 ``plan_step.script_name/script_version`` 与
``script`` 表的存在，不依赖完整 schema。
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from testcontainers.postgres import PostgresContainer

from backend.services.script_seed_governance import (
    count_plan_step_references,
    raise_if_any_version_referenced,
    raise_if_version_referenced,
)


def _normalize(url: str) -> str:
    return url.replace("postgresql+psycopg2://", "postgresql+psycopg://", 1)


@pytest.fixture(scope="module")
def pg_engine():
    with PostgresContainer("postgres:16") as postgres:
        engine = create_engine(_normalize(postgres.get_connection_url()))
        with engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE TABLE plan_step ("
                    " id serial PRIMARY KEY,"
                    " plan_id integer,"
                    " script_name varchar(64) NOT NULL,"
                    " script_version varchar(32) NOT NULL)"
                )
            )
            conn.execute(
                text(
                    "CREATE TABLE script ("
                    " id serial PRIMARY KEY,"
                    " name varchar(64) NOT NULL,"
                    " version varchar(32) NOT NULL,"
                    " is_active boolean NOT NULL DEFAULT true)"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO script (name, version, is_active) VALUES "
                    "('flash_firmware', '1.3.1', true), "
                    "('flash_firmware', '1.3.0', true)"
                )
            )
        yield engine
        engine.dispose()


def test_unreferenced_version_passes(pg_engine):
    """无引用 → 模板放行（种子覆写/停用可执行）。"""
    with pg_engine.connect() as conn:
        assert (
            count_plan_step_references(
                conn, script_name="flash_firmware", script_version="1.3.0"
            )
            == 0
        )
        raise_if_version_referenced(
            conn, script_name="flash_firmware", script_version="1.3.0"
        )


def test_referenced_version_aborts_with_guidance(pg_engine):
    """有引用 → RuntimeError 且指引可读（迁移失败，重指后重跑）。"""
    with pg_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO plan_step (plan_id, script_name, script_version) "
                "VALUES (1, 'flash_firmware', '1.3.1')"
            )
        )
    with pytest.raises(RuntimeError, match="仍被 1 个 plan_step 引用"):
        with pg_engine.connect() as conn:
            raise_if_version_referenced(
                conn, script_name="flash_firmware", script_version="1.3.1"
            )


def test_batch_deactivate_aborts_listing_all_blocked(pg_engine):
    """批量停用形态：任一被引用即失败，错误列出全部被堵版本。"""
    with pg_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO plan_step (plan_id, script_name, script_version) "
                "VALUES (2, 'flash_firmware', '1.3.0')"
            )
        )
    with pytest.raises(RuntimeError) as exc_info:
        with pg_engine.connect() as conn:
            raise_if_any_version_referenced(
                conn,
                script_name="flash_firmware",
                versions=["1.3.0", "1.3.1"],
            )
    # 两个被堵版本都在指引里
    assert "1.3.0 ×1" in str(exc_info.value)
    assert "1.3.1 ×1" in str(exc_info.value)
