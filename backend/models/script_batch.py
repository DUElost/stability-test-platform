"""Script batch execution models: lightweight per-device script sequences."""

from datetime import datetime

from sqlalchemy import (
    Column, DateTime, ForeignKey, Index, Integer, String, Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from backend.core.database import Base


class ScriptBatch(Base):
    """One batch = one device × one ordered script sequence."""

    __tablename__ = "script_batch"

    id          = Column(Integer, primary_key=True)
    name        = Column(String(256), nullable=True)
    sequence_id = Column(Integer, ForeignKey("script_sequence.id"), nullable=True)
    device_id   = Column(Integer, ForeignKey("device.id"), nullable=False)
    host_id     = Column(String(64), ForeignKey("host.id"), nullable=True)
    status      = Column(String(32), nullable=False, default="PENDING")
    # PENDING → RUNNING → COMPLETED / FAILED / PARTIAL
    on_failure  = Column(String(16), nullable=False, default="stop")
    log_dir     = Column(String(512), nullable=True)

    # ── Watcher lifecycle ──
    watcher_started_at  = Column(DateTime(timezone=True))
    watcher_stopped_at  = Column(DateTime(timezone=True))
    watcher_capability  = Column(String(32))
    log_signal_count    = Column(Integer, nullable=False, default=0)

    started_at  = Column(DateTime(timezone=True))
    ended_at    = Column(DateTime(timezone=True))
    created_at  = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)

    device   = relationship("Device", foreign_keys=[device_id])
    host     = relationship("Host", foreign_keys=[host_id])
    sequence = relationship("ScriptSequence", foreign_keys=[sequence_id])
    runs     = relationship("ScriptRun", back_populates="batch", lazy="selectin",
                            order_by="ScriptRun.item_index")

    __table_args__ = (
        Index("idx_script_batch_status", "status"),
        Index("idx_script_batch_device", "device_id"),
        Index("idx_script_batch_host", "host_id"),
    )


class ScriptRun(Base):
    """Single script execution within a batch."""

    __tablename__ = "script_run"

    id             = Column(Integer, primary_key=True)
    batch_id       = Column(Integer, ForeignKey("script_batch.id"), nullable=False)
    item_index     = Column(Integer, nullable=False, default=0)
    script_name    = Column(String(128), nullable=False)
    script_version = Column(String(32), nullable=False)
    params_json    = Column(JSONB, nullable=False, default=dict)
    status         = Column(String(32), nullable=False, default="PENDING")
    # PENDING → RUNNING → COMPLETED / FAILED / SKIPPED
    exit_code      = Column(Integer, nullable=True)
    stdout         = Column(Text, nullable=True)
    stderr         = Column(Text, nullable=True)
    metrics_json   = Column(JSONB, nullable=True)
    started_at     = Column(DateTime(timezone=True))
    ended_at       = Column(DateTime(timezone=True))
    created_at     = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)

    batch = relationship("ScriptBatch", foreign_keys=[batch_id], back_populates="runs")

    __table_args__ = (
        Index("idx_script_run_batch", "batch_id"),
    )
