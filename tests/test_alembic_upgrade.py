from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from testcontainers.postgres import PostgresContainer


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"

# ADR-0020 之前、status 列还是 VARCHAR 的那个版本
_PRE_STATUS_ENUM_REVISION = "l2m3n4o5p6q7"


def _normalize_database_url(database_url: str) -> str:
    return database_url.replace("postgresql+psycopg2://", "postgresql+psycopg://", 1)


def _alembic(env: dict, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(BACKEND_DIR / "alembic.ini"), *args],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_alembic_upgrade_head_succeeds_from_pre_status_enum_schema():
    """从 pre-status-enum 版本(l2m3n4o5p6q7)升到 head 必须成功。

    起点用 `alembic upgrade l2m3n4o5p6q7` 真实回放到那个版本,而不是手写
    一份简化 schema —— 手写版只建了 plan_run / job_instance,漏掉了 host,
    于是 w1x2y3z4a5b6 往 host 加列时炸在 UndefinedTable。真实库在该版本上
    是有 host 的,所以那是 fixture 不完整,不是迁移有问题。回放还有个好处:
    以后新增迁移不必再回来手工补表。
    """
    with PostgresContainer("postgres:16") as postgres:
        env = os.environ.copy()
        env["DATABASE_URL"] = _normalize_database_url(postgres.get_connection_url())

        to_baseline = _alembic(env, "upgrade", _PRE_STATUS_ENUM_REVISION)
        assert to_baseline.returncode == 0, (
            f"回放到 {_PRE_STATUS_ENUM_REVISION} 失败:\n{to_baseline.stderr}"
        )

        result = _alembic(env, "upgrade", "head")

    assert result.returncode == 0, result.stderr
