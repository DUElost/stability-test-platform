"""FinishingGuard global admission gate — ADR-0065 D5.8 / B12 U1.

Single-row table: ``id`` is fixed to ``1``. Rollback close path (U4) takes
``FOR UPDATE`` on this row, sets ``admission_open=false``, and drains active
pre_registered / granted jobs before switching consumers.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, CheckConstraint, Column, DateTime, Integer, String, text

from backend.core.database import Base


class FinishingGuardAdmission(Base):
    __tablename__ = "finishing_guard_admission"

    id = Column(Integer, primary_key=True)
    admission_open = Column(
        Boolean, nullable=False, default=True, server_default=text("true"),
    )
    closed_at = Column(DateTime(timezone=True), nullable=True)
    closed_reason = Column(String(128), nullable=True)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        server_default=text("now()"),
    )

    __table_args__ = (
        CheckConstraint("id = 1", name="ck_finishing_guard_admission_singleton"),
    )


# Fixed primary key for the singleton gate row.
FINISHING_GUARD_ADMISSION_ROW_ID = 1
