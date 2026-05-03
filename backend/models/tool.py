from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text, UniqueConstraint
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
    category     = Column(String(64))
    created_at   = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at   = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        UniqueConstraint("name", "version", name="uq_tool_name_version"),
    )
