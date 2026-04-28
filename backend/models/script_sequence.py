from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB

from backend.core.database import Base


class ScriptSequence(Base):
    __tablename__ = "script_sequence"

    id = Column(Integer, primary_key=True)
    name = Column(String(256), nullable=False)
    description = Column(Text)
    items = Column(JSONB, nullable=False)
    on_failure = Column(String(16), nullable=False, default="stop")
    created_by = Column(String(128))
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )
