from datetime import datetime

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB

from backend.core.database import Base


class WorkflowDefinition(Base):
    __tablename__ = "workflow_definition"

    id                = Column(Integer, primary_key=True)
    name              = Column(String(256), nullable=False)
    description       = Column(Text)
    failure_threshold = Column(Float, nullable=False, default=0.05)
    created_by        = Column(String(128))
    created_at        = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at        = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class WorkflowRun(Base):
    __tablename__ = "workflow_run"

    id                     = Column(Integer, primary_key=True)
    workflow_definition_id = Column(Integer, ForeignKey("workflow_definition.id"), nullable=False)
    status                 = Column(String(32), nullable=False, default="RUNNING")
    failure_threshold      = Column(Float, nullable=False, default=0.05)
    triggered_by           = Column(String(128))
    started_at             = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    ended_at               = Column(DateTime(timezone=True))
    result_summary         = Column(JSONB)
