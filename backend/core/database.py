import os
from functools import lru_cache

from sqlalchemy import create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker

from .database_adapter import init_adapter, get_adapter_from_engine

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./stability.db")


def _create_engine(database_url: str):
    """创建数据库引擎，根据数据库类型配置不同参数"""
    connect_args = {}

    if database_url.startswith("sqlite"):
        connect_args = {"check_same_thread": False}
        # SQLite 不支持多线程连接池，使用 NullPool
        from sqlalchemy.pool import NullPool
        engine = create_engine(
            database_url,
            pool_pre_ping=True,
            future=True,
            connect_args=connect_args,
            poolclass=NullPool,
        )
    elif database_url.startswith("postgresql"):
        # PostgreSQL 连接池配置
        engine = create_engine(
            database_url,
            pool_pre_ping=True,
            future=True,
            pool_size=10,
            max_overflow=20,
            pool_recycle=3600,
            pool_timeout=30,
        )
    else:
        engine = create_engine(
            database_url,
            pool_pre_ping=True,
            future=True,
        )

    return engine


engine = _create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, future=True)
Base = declarative_base()


# 初始化数据库适配器
@event.listens_for(engine, "connect")
def on_connect(dbapi_conn, connection_record):
    """连接建立时的处理"""
    adapter = get_adapter_from_engine(engine)
    adapter.on_connect(dbapi_conn, connection_record)


# 延迟初始化适配器
_init_done = False


def ensure_adapter_initialized():
    """确保适配器已初始化"""
    global _init_done
    if not _init_done:
        init_adapter(engine)
        _init_done = True


def get_db():
    """获取数据库会话"""
    ensure_adapter_initialized()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_raw_db():
    """获取原始数据库会话（用于后台任务）"""
    ensure_adapter_initialized()
    return SessionLocal()
