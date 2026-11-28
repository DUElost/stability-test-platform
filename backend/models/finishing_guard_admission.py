"""Global singleton admission gate for FinishingGuard v2 rollback barrier (ADR-0065 / B12 U4)."""

from __future__ import annotations

from sqlalchemy import Boolean, CheckConstraint, Column, Integer

from backend.core.database import Base

ADMISSION_ROW_ID = 1


class FinishingGuardAdmission(Base):
    __tablename__ = "finishing_guard_admission"

    id = Column(Integer, primary_key=True)
    admission_open = Column(Boolean, nullable=False, server_default="true")

    __table_args__ = (
        CheckConstraint("id = 1", name="ck_finishing_guard_admission_singleton"),
    )