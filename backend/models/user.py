from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Integer, String

from backend.core.database import Base


class User(Base):
    """User model for system authentication."""

    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    username = Column(String(128), unique=True, nullable=False, index=True)
    hashed_password = Column(String(256), nullable=False)
    role = Column(String(32), default="user", nullable=False)
    is_active = Column(String(1), default="Y", nullable=False)
    # R02-D2（#902）会话纪元：改密/重置/停用/改角色即递增，全部在发
    # token（携带旧 ver claim）立即失效。server_default 使 ALTER 对存量行
    # 即时生效（PG 11+ 元数据级）。
    token_version = Column(Integer, default=1, nullable=False, server_default="1")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    last_login = Column(DateTime)
