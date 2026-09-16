from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine
from testcontainers.postgres import PostgresContainer


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
INIT_DEV_DB = BACKEND_DIR / "scripts" / "init_dev_db.py"


def _head_revision() -> str:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    return ScriptDirectory.from_config(cfg).get_current_head()

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


def test_dev_bootstrap_from_empty_database_produces_seeded_schema():
    """#2381：compose 的 dev 库入口从空库必须带出字典 seed。

    用**脚本方式**调用（compose 的真实形态 `python /app/backend/scripts/init_dev_db.py`，
    `sys.path[0]` 是脚本目录），并刻意把 cwd 放在仓库外——两条都踩过：
    顶层 `import backend.*` 在这种调用形态下直接 ModuleNotFoundError，而 lint 全绿。

    `create_all` 的产物是「38 张表 + 0 行 specialty」，看起来完全像 schema 成功；
    这里的地面真值判据就是 specialty 与 alembic_version。
    """
    with PostgresContainer("postgres:16") as postgres:
        url = _normalize_database_url(postgres.get_connection_url())
        env = os.environ.copy()
        env.update({
            "TESTING": "1",
            "ENV": "development",
            "JWT_SECRET_KEY": "dev-bootstrap-test",
            "DATABASE_URL": url,
            "STP_ADMIN_USER": "admin",
            "STP_ADMIN_PASSWORD": "dev-bootstrap-pw",
        })

        def run() -> subprocess.CompletedProcess:
            return subprocess.run(
                [sys.executable, str(INIT_DEV_DB)],
                env=env,
                cwd=str(Path("/tmp").resolve()),
                capture_output=True,
                text=True,
                check=False,
            )

        first = run()
        assert first.returncode == 0, f"dev bootstrap 失败:\n{first.stdout}\n{first.stderr}"
        assert "dev_db_schema_ready path=alembic" in first.stdout, first.stdout
        assert "create_all_legacy" not in first.stdout, (
            "空库被判成 legacy 兜底 —— 正是 #2381 的失效形态"
        )

        engine = create_engine(url)
        try:
            with engine.connect() as conn:
                specialty = conn.exec_driver_sql("select count(*) from specialty").scalar()
                version = conn.exec_driver_sql(
                    "select version_num from alembic_version"
                ).scalar()
                scripts = conn.exec_driver_sql("select count(*) from script").scalar()
                admin = conn.exec_driver_sql(
                    "select count(*) from users where username = 'admin'"
                ).scalar()
        finally:
            engine.dispose()

        assert specialty > 0, (
            f"空库 bootstrap 后 specialty={specialty}：Plan 表单没有专项可选（#2381 本体）"
        )
        assert scripts > 0, "脚本注册字典未 seed，Plan 步骤无可选项"
        assert version == _head_revision(), f"停在 {version}，未到 head"
        assert admin == 1, "admin 账号未 upsert"

        # 幂等：compose `up` 反复执行是常态，第二次不得炸、也不得掉 seed
        second = run()
        assert second.returncode == 0, second.stderr
        assert "path=alembic" in second.stdout
        engine = create_engine(url)
        try:
            with engine.connect() as conn:
                assert conn.exec_driver_sql("select count(*) from specialty").scalar() == specialty
        finally:
            engine.dispose()
