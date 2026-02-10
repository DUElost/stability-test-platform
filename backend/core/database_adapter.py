"""
数据库适配器模块
提供数据库兼容性抽象，支持 SQLite 和 PostgreSQL
"""

import logging
from abc import ABC, abstractmethod
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Query, Session

logger = logging.getLogger(__name__)


class DatabaseAdapter(ABC):
    """数据库适配器抽象基类"""

    @property
    @abstractmethod
    def name(self) -> str:
        """适配器名称"""
        pass

    @abstractmethod
    def supports_skip_locked(self) -> bool:
        """是否支持 FOR UPDATE SKIP LOCKED"""
        pass

    @abstractmethod
    def apply_for_update(self, query: Query, skip_locked: bool = False) -> Query:
        """
        应用 FOR UPDATE 子句

        Args:
            query: SQLAlchemy 查询对象
            skip_locked: 是否使用 SKIP LOCKED (仅在支持的数据库上有效)

        Returns:
            修改后的查询对象
        """
        pass

    @abstractmethod
    def get_now_expression(self) -> str:
        """获取当前时间的数据库表达式"""
        pass

    @abstractmethod
    def get_json_type(self) -> str:
        """获取 JSON 类型的数据库类型名"""
        pass

    def on_connect(self, dbapi_conn, connection_record):
        """连接建立时的回调，可用于设置数据库特定参数"""
        pass


class PostgreSQLAdapter(DatabaseAdapter):
    """PostgreSQL 适配器"""

    @property
    def name(self) -> str:
        return "postgresql"

    def supports_skip_locked(self) -> bool:
        return True

    def apply_for_update(self, query: Query, skip_locked: bool = False) -> Query:
        if skip_locked:
            return query.with_for_update(skip_locked=True)
        return query.with_for_update()

    def get_now_expression(self) -> str:
        return "NOW()"

    def get_json_type(self) -> str:
        return "JSONB"

    def on_connect(self, dbapi_conn, connection_record):
        """设置 PostgreSQL 特定参数"""
        # 设置时区为 UTC
        with dbapi_conn.cursor() as cursor:
            cursor.execute("SET TIME ZONE 'UTC'")


class SQLiteAdapter(DatabaseAdapter):
    """SQLite 适配器"""

    @property
    def name(self) -> str:
        return "sqlite"

    def supports_skip_locked(self) -> bool:
        return False

    def apply_for_update(self, query: Query, skip_locked: bool = False) -> Query:
        # SQLite 不支持 FOR UPDATE，但支持事务隔离
        # 返回原始查询，依赖事务隔离级别
        logger.debug("SQLite does not support FOR UPDATE, using transaction isolation")
        return query

    def get_now_expression(self) -> str:
        return "DATETIME('now')"

    def get_json_type(self) -> str:
        return "TEXT"

    def on_connect(self, dbapi_conn, connection_record):
        """设置 SQLite 特定参数"""
        # 启用外键约束
        dbapi_conn.execute("PRAGMA foreign_keys = ON")
        # 设置 WAL 模式提高并发性能
        dbapi_conn.execute("PRAGMA journal_mode = WAL")
        dbapi_conn.execute("PRAGMA synchronous = NORMAL")


def get_adapter(dialect_name: str) -> DatabaseAdapter:
    """
    根据数据库方言名称获取适配器

    Args:
        dialect_name: 数据库方言名称，如 'postgresql', 'sqlite'

    Returns:
        对应的数据库适配器实例

    Raises:
        ValueError: 如果方言不受支持
    """
    adapters = {
        "postgresql": PostgreSQLAdapter,
        "sqlite": SQLiteAdapter,
    }

    adapter_class = adapters.get(dialect_name.lower())
    if not adapter_class:
        raise ValueError(f"Unsupported database dialect: {dialect_name}")

    return adapter_class()


def get_adapter_from_engine(engine) -> DatabaseAdapter:
    """
    从 SQLAlchemy 引擎获取适配器

    Args:
        engine: SQLAlchemy 引擎实例

    Returns:
        对应的数据库适配器实例
    """
    dialect_name = engine.dialect.name
    return get_adapter(dialect_name)


# 全局适配器实例（延迟初始化）
_adapter: Optional[DatabaseAdapter] = None


def init_adapter(engine):
    """初始化全局适配器"""
    global _adapter
    _adapter = get_adapter_from_engine(engine)
    logger.info(f"Database adapter initialized: {_adapter.name}")


def get_current_adapter() -> DatabaseAdapter:
    """获取当前全局适配器"""
    if _adapter is None:
        raise RuntimeError("Database adapter not initialized. Call init_adapter() first.")
    return _adapter
