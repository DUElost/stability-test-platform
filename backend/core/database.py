import importlib.util
import os
from typing import Dict

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from backend.core.env_source import resolve_database_url

# 唯一解析入口是 env_source：ambient env → 仓库根 .env.backend，**绝无兜底默认**。
# 解析不到直接 RuntimeError——曾经这里的默认值是
# `postgresql+asyncpg://stp:password@localhost:5432/stp`（直接点名生产库，
# 只靠密码占位符侥幸没连上）。现在宁可拒绝启动，也不能猜。
DATABASE_URL, _ = resolve_database_url()


def is_sqlite_url(database_url: str) -> bool:
    return database_url.startswith("sqlite")


def normalize_sync_database_url(database_url: str) -> str:
    if database_url.startswith("postgresql+asyncpg://"):
        return database_url.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)
    if database_url.startswith("postgresql+psycopg2://"):
        return database_url.replace("postgresql+psycopg2://", "postgresql+psycopg://", 1)
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    if database_url.startswith("sqlite+aiosqlite://"):
        return database_url.replace("sqlite+aiosqlite://", "sqlite://", 1)
    return database_url


def normalize_async_database_url(database_url: str) -> str:
    if database_url.startswith("postgresql+psycopg://"):
        return database_url.replace("postgresql+psycopg://", "postgresql+asyncpg://", 1)
    if database_url.startswith("postgresql+psycopg2://"):
        return database_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if database_url.startswith("sqlite://"):
        return database_url.replace("sqlite://", "sqlite+aiosqlite://", 1)
    return database_url


def _pool_env_int(name: str, default: int) -> int:
    """连接池容量 env 读取（#1516）。

    非法值/非正值回退默认：连接池容量误配（0、负数、拼写错误）比回退默认更危险
    ——`pool_size=0` 会让每次借连接都直接抛 `QueuePool limit ... reached`。
    """
    raw = (os.getenv(name) or "").strip()
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _pool_capacity_kwargs() -> Dict[str, object]:
    """同步/异步引擎共用的池容量参数（同源 env 驱动，默认 30 / 60 / 1800）."""
    return {
        "pool_size": _pool_env_int("STP_DB_POOL_SIZE", 30),
        "max_overflow": _pool_env_int("STP_DB_MAX_OVERFLOW", 60),
        "pool_recycle": _pool_env_int("STP_DB_POOL_RECYCLE", 1800),
    }


def get_async_engine_kwargs(database_url: str) -> Dict[str, object]:
    if is_sqlite_url(database_url):
        return {}
    return {
        "pool_pre_ping": True,
        **_pool_capacity_kwargs(),
    }


def get_sync_engine_kwargs(database_url: str) -> Dict[str, object]:
    """同步引擎 kwargs（#1516）。

    同步池原先只设 ``pool_pre_ping``，容量回退 SQLAlchemy ``QueuePool`` 默认
    ``5 + 10 = 15``；而共享该池的是 12 个 APScheduler 周期任务、SAQ 默认并发 10
    与 84 处 ``SessionLocal()`` 调用点。触顶后经
    ``leader_election`` 的 fail-closed 设计会连锁跳过全部 singleton 调度
    （Recycler / Reconciler / Watchdog），作业卡在 RUNNING/UNKNOWN 无自愈出口。
    异步池独立，带宽裕救不了同步侧，故两侧同源 env 驱动、默认同值。
    """
    kwargs: Dict[str, object] = {"future": True}
    if not is_sqlite_url(database_url):
        kwargs["pool_pre_ping"] = True
        kwargs.update(_pool_capacity_kwargs())
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
