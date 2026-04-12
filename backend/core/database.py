import importlib.util
import os
from typing import Dict

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://stp:password@localhost:5432/stp")


def is_sqlite_url(database_url: str) -> bool:
    return database_url.startswith("sqlite")


def normalize_sync_database_url(database_url: str) -> str:
    if database_url.startswith("postgresql+asyncpg://"):
        return database_url.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    if database_url.startswith("sqlite+aiosqlite://"):
        return database_url.replace("sqlite+aiosqlite://", "sqlite://", 1)
    return database_url


def normalize_async_database_url(database_url: str) -> str:
    if database_url.startswith("postgresql+psycopg://"):
        return database_url.replace("postgresql+psycopg://", "postgresql+asyncpg://", 1)
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if database_url.startswith("sqlite://"):
        return database_url.replace("sqlite://", "sqlite+aiosqlite://", 1)
    return database_url


def get_async_engine_kwargs(database_url: str) -> Dict[str, object]:
    if is_sqlite_url(database_url):
        return {}
    return {
        "pool_pre_ping": True,
        "pool_size": 10,
        "max_overflow": 20,
        "pool_recycle": 3600,
    }


def get_sync_engine_kwargs(database_url: str) -> Dict[str, object]:
    kwargs: Dict[str, object] = {"future": True}
    if not is_sqlite_url(database_url):
        kwargs["pool_pre_ping"] = True
    return kwargs


def has_aiosqlite() -> bool:
    return importlib.util.find_spec("aiosqlite") is not None


class _MissingAsyncConnectionContext:
    async def __aenter__(self):
        raise RuntimeError(
            "SQLite quick-test mode requires 'aiosqlite' for async database access. "
            "Install backend requirements or use TEST_DATABASE_URL with PostgreSQL."
        )

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _MissingAsyncEngine:
    def connect(self):
        return _MissingAsyncConnectionContext()

    async def dispose(self):
        return None


class _MissingAsyncSessionContext:
    async def __aenter__(self):
        raise RuntimeError(
            "Async DB session is unavailable in SQLite quick-test mode without 'aiosqlite'."
        )

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _MissingAsyncSessionFactory:
    def __call__(self, *args, **kwargs):
        return _MissingAsyncSessionContext()


_sync_url = normalize_sync_database_url(DATABASE_URL)
_async_url = normalize_async_database_url(DATABASE_URL)
_use_missing_async_runtime = (
    os.getenv("TESTING") == "1"
    and is_sqlite_url(DATABASE_URL)
    and not has_aiosqlite()
)

if _use_missing_async_runtime:
    # 本地 SQLite 快速回归允许导入应用，但显式禁止需要真实异步驱动的路径。
    async_engine = _MissingAsyncEngine()
    AsyncSessionLocal = _MissingAsyncSessionFactory()
else:
    async_engine = create_async_engine(_async_url, **get_async_engine_kwargs(_async_url))
    AsyncSessionLocal = async_sessionmaker(
        async_engine, class_=AsyncSession, expire_on_commit=False
    )

# ── Sync engine (Alembic migrations + legacy API routes) ──
engine = create_engine(_sync_url, **get_sync_engine_kwargs(_sync_url))
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, future=True)

Base = declarative_base()


def get_db():
    """Sync session — used by all legacy routes that haven't migrated yet."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


async def get_async_db() -> AsyncSession:
    """Async session — used by new Phase 2+ routes."""
    async with AsyncSessionLocal() as session:
        yield session
