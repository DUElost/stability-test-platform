"""DEV ONLY: bring the Docker Compose database up to a usable, **seeded** state.

The Alembic chain *can* bootstrap an empty database (it has been able to since
well before this note; #2381 measured `alembic upgrade head` from empty on two
scratch databases and got 6 `specialty` rows plus the script registry).  The old
docstring claimed the opposite, so this script had been creating the schema via
``Base.metadata.create_all()`` — which produces 38 tables and **zero dictionary
seed rows**, because the only source of truth for those dictionaries is the
seed migrations.  With an empty `specialty` table the Plan form has no value to
submit and the API has no write endpoint for it, so a fresh dev environment
cannot create a single Plan while looking like a schema problem.

So Alembic is now the primary path and ``create_all`` survives only as the
legacy-dev-database fallback (a database that already has tables but no
``alembic_version`` row cannot be adopted by the chain without an explicit
``alembic stamp`` decision).  That fallback is *labelled* in its output: an
unlabelled stopgap is how #2381 stayed hidden.

Still non-production only — see ``_refuse_production``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _refuse_production() -> None:
    if os.getenv("ENV", "").strip().lower() == "production":
        raise SystemExit("Refusing to initialize dev DB when ENV=production")


def _import_models() -> None:
    import backend.models.audit  # noqa: F401
    import backend.models.device_lease  # noqa: F401
    import backend.models.host  # noqa: F401
    import backend.models.jira_run  # noqa: F401
    import backend.models.job  # noqa: F401
    import backend.models.notification  # noqa: F401
    import backend.models.plan  # noqa: F401
    import backend.models.plan_migration_audit  # noqa: F401
    import backend.models.plan_run  # noqa: F401
    import backend.models.plan_run_artifact  # noqa: F401
    import backend.models.resource_pool  # noqa: F401
    import backend.models.schedule  # noqa: F401
    import backend.models.script  # noqa: F401
    import backend.models.token_blacklist  # noqa: F401
    import backend.models.user  # noqa: F401


def _upsert_admin() -> None:
    username = os.getenv("STP_ADMIN_USER", "admin").strip()
    password = os.getenv("STP_ADMIN_PASSWORD", "").strip()
    if not username or not password:
        print("dev_db_admin_skipped reason=missing STP_ADMIN_USER/STP_ADMIN_PASSWORD")
        return

    from backend.core.database import SessionLocal
    from backend.core.security import get_password_hash
    from backend.models.user import User

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == username).first()
        if user is None:
            user = User(
                username=username,
                hashed_password=get_password_hash(password),
                role="admin",
                is_active="Y",
            )
            db.add(user)
            action = "created"
        else:
            user.hashed_password = get_password_hash(password)
            user.role = "admin"
            user.is_active = "Y"
            action = "updated"
        db.commit()
        print(f"dev_db_admin_{action} username={username!r}")
    finally:
        db.close()


_PATH_ALEMBIC = "alembic"
_PATH_CREATE_ALL_LEGACY = "create_all_legacy"


def _choose_bootstrap_path(tables: set[str]) -> str:
    """dev 库该走哪条 schema 通道。纯函数（表名集合 → 路径标签），便于无库判据。

    - 空库 → ``alembic upgrade head``：唯一能带出字典 seed 的路径（#2381）；
    - 已有 ``alembic_version`` → 同样走链，正常增量升级；
    - 有表但没有 ``alembic_version`` → 当年纯 ``create_all`` 建出来的老 dev 库。链在
      这种库上会撞「表已存在」，收养它需要显式 ``alembic stamp`` 决策，不该由一个 dev
      脚本顺手做掉，因此保持旧行为**并显式标注**（缺 seed 的代价见输出）。
    """
    if not tables or "alembic_version" in tables:
        return _PATH_ALEMBIC
    return _PATH_CREATE_ALL_LEGACY


def _bootstrap_schema() -> str:
    """把 dev 库带到「有 schema 且有 seed」。返回实际走的路径，便于断言与排障。"""
    from sqlalchemy import inspect

    # 必须在 sys.path 注入之后导入：本文件既被 `python backend/scripts/init_dev_db.py`
    # 以脚本方式调用（docker-compose 就是这种，sys.path[0] 是脚本目录），也会被测试
    # 以模块方式导入，顶层 import backend.* 在脚本方式下会直接 ModuleNotFoundError。
    from backend.core.database import Base, engine
    from backend.core.env_source import resolve_database_url

    url, _source = resolve_database_url()
    tables = set(inspect(engine).get_table_names())

    if _choose_bootstrap_path(tables) == _PATH_ALEMBIC:
        # 复用 check_schema_sync 的同一条通道：它带着 #934 的 ambient-DATABASE_URL
        # 处理（alembic.ini 自带 sqlite 占位，env.py 在导入期用环境解析结果覆写
        # config URL）。把这段重新实现一遍，等于再造一个「连错库」的可能性。
        from backend.scripts.check_schema_sync import _run_upgrade

        _run_upgrade(url)
        return _PATH_ALEMBIC

    Base.metadata.create_all(bind=engine)
    print(
        "dev_db_schema_ready path=create_all_legacy WARNING=no_alembic_version "
        "dictionary_seeds_not_applied: 该库当年由 create_all 建出、没有 alembic_version，"
        "specialty 等静态字典不会补齐（无写端点，变更只能走迁移），"
        "因此「新建 Plan 无专项可选」会复现（#2381）。"
        "要接回迁移链：先确认库内 schema 与 head 一致，再 `alembic stamp <revision>` "
        "后 `alembic upgrade head`；或直接重建一个空 dev 库。"
    )
    return _PATH_CREATE_ALL_LEGACY


def main() -> int:
    _refuse_production()
    _import_models()

    path = _bootstrap_schema()
    if path == _PATH_ALEMBIC:
        print("dev_db_schema_ready path=alembic")
    _upsert_admin()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
