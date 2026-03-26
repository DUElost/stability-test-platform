from datetime import datetime

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
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_login = Column(DateTime)
