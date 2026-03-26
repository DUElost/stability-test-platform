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

# Override DB URL from environment (use psycopg sync driver for Alembic)
_db_url = os.getenv("DATABASE_URL", "postgresql+psycopg://stp:password@localhost:5432/stp")
_sync_url = _db_url.replace("postgresql+asyncpg://", "postgresql+psycopg://")
config.set_main_option("sqlalchemy.url", _sync_url)


def get_metadata():
    from backend.core.database import Base  # noqa: F401 — import all models for metadata
    import backend.models.action_template  # noqa: F401
    import backend.models.audit            # noqa: F401
    import backend.models.enums            # noqa: F401
    import backend.models.host             # noqa: F401
    import backend.models.job              # noqa: F401
    import backend.models.notification     # noqa: F401
    import backend.models.schedule         # noqa: F401
    import backend.models.tool             # noqa: F401
    import backend.models.user             # noqa: F401
    import backend.models.workflow         # noqa: F401
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
