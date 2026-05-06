"""Plan migration audit trail — ADR-0020 Phase 3-4.

Tracks the mapping between old WorkflowDefinition/TaskTemplate and new Plan
rows for audit and troubleshooting.  Retain for at least 6 months before
archiving.
"""

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Integer, Text

from backend.core.database import Base


class PlanMigrationAudit(Base):
    __tablename__ = "plan_migration_audit"

    id                         = Column(Integer, primary_key=True)
    old_workflow_definition_id = Column(Integer, nullable=True)
    old_task_template_id       = Column(Integer, nullable=True)
    new_plan_id                = Column(Integer, nullable=False)
    chain_index                = Column(Integer, nullable=True)
    note                       = Column(Text)
    created_at                 = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
