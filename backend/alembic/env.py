import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine, pool

# Add project root (parent of backend/) to sys.path so `import backend.*` works
project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _resolve_database_url() -> str:
    """Resolve the migration target, loudly, from a single source.

    Delegates to :mod:`backend.core.env_source` so the rule ("ambient env, then
    the repo-root ``.env.backend``, never ``backend/.env``, never a built-in
    default") lives in exactly one place and is unit-testable. See that module
    for why refusing to guess matters more than asserting the database name.
    """
    from backend.core.env_source import redact_url, resolve_database_url

    url, source = resolve_database_url()
    # Echo the target before connecting, password stripped. Landing on the
    # wrong database should be visible in the log *before* any DDL runs.
    # stderr 直写：此处早于 alembic 的 logging 配置，用 logger 会被吞掉。
    sys.stderr.write(f"[alembic] target={redact_url(url)} (from {source})\n")
    # 写回环境：get_metadata() 随后会 import backend.core.database，而那里
    # 仍有 os.getenv("DATABASE_URL", "…@localhost:5432/stp") 的兜底默认 ——
    # 不写回的话，从 .env.backend 解析出的目标只作用于 alembic 自己的连接，
    # 被 import 的模块会各自建一个指向那个默认值（生产库名）的 engine。
    os.environ["DATABASE_URL"] = url
    return url


# Override DB URL from environment (use psycopg sync driver for Alembic)
_db_url = _resolve_database_url()
_sync_url = _db_url.replace("postgresql+asyncpg://", "postgresql+psycopg://")
config.set_main_option("sqlalchemy.url", _sync_url)


def get_metadata():
    from backend.core.database import Base  # noqa: F401 — import all models for metadata
    import backend.models.action_template      # noqa: F401
    import backend.models.audit                # noqa: F401
    import backend.models.device_lease         # noqa: F401 — ADR-0019 Phase 1
    import backend.models.enums                # noqa: F401
    import backend.models.host                 # noqa: F401
    import backend.models.job                  # noqa: F401
    import backend.models.notification         # noqa: F401
    import backend.models.plan                 # noqa: F401 — ADR-0020
    import backend.models.plan_migration_audit # noqa: F401 — ADR-0020 迁移审计
    import backend.models.plan_run             # noqa: F401 — ADR-0020
    import backend.models.resource_pool        # noqa: F401
    import backend.models.schedule             # noqa: F401
    import backend.models.script               # noqa: F401
    import backend.models.token_blacklist       # noqa: F401 — ADR-0024 revoked_refresh_token
    import backend.models.user                 # noqa: F401
    return Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=get_metadata(),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(
        config.get_main_option("sqlalchemy.url"),
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=get_metadata())
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
