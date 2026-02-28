import os

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://stp:password@localhost:5432/stp")

# Sync URL: psycopg driver for Alembic and legacy sync routes
_sync_url = (
    DATABASE_URL
    .replace("postgresql+asyncpg://", "postgresql+psycopg://")
    .replace("postgresql://", "postgresql+psycopg://")
)

# ── Async engine (new services: heartbeat_monitor, aggregator, future API routes) ──
async_engine = create_async_engine(
    DATABASE_URL if "asyncpg" in DATABASE_URL else DATABASE_URL.replace("postgresql+psycopg://", "postgresql+asyncpg://").replace("postgresql://", "postgresql+asyncpg://"),
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    pool_recycle=3600,
)
AsyncSessionLocal = async_sessionmaker(
    async_engine, class_=AsyncSession, expire_on_commit=False
)

# ── Sync engine (Alembic migrations + legacy API routes) ──
engine = create_engine(_sync_url, pool_pre_ping=True, future=True)
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
