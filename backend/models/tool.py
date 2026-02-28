from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB

from backend.core.database import Base


class Tool(Base):
    __tablename__ = "tool"

    id           = Column(Integer, primary_key=True)
    name         = Column(String(128), nullable=False)
    version      = Column(String(32), nullable=False)
    script_path  = Column(Text, nullable=False)
    script_class = Column(String(128), nullable=False)
    param_schema = Column(JSONB, nullable=False, default=dict)
    is_active    = Column(Boolean, nullable=False, default=True)
    description  = Column(Text)
    created_at   = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at   = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        __import__("sqlalchemy").UniqueConstraint("name", "version", name="uq_tool_name_version"),
    )
