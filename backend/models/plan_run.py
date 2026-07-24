"""PlanRun ORM — ADR-0020.

Every execution of a Plan (manual, cron, or chain-triggered) produces one
PlanRun.  Multi-Plan chains produce one PlanRun per segment, linked by
parent_plan_run_id / root_plan_run_id.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Enum as SAEnum,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from backend.core.database import Base
from backend.models.enums import PlanRunStatus

PLAN_RUN_STATUS_DB_ENUM = SAEnum(
    *(status.value for status in PlanRunStatus),
    name="plan_run_status",
    validate_strings=True,
)


class PlanRun(Base):
    __tablename__ = "plan_run"

    id                = Column(Integer, primary_key=True)
    plan_id           = Column(Integer, ForeignKey("plan.id"), nullable=False)
    status            = Column(PLAN_RUN_STATUS_DB_ENUM, nullable=False, default=PlanRunStatus.RUNNING.value)
    failure_threshold = Column(Float, nullable=False, default=0.05)
    plan_snapshot     = Column(JSONB, nullable=False)
    run_type          = Column(String(16), nullable=False)
    run_context       = Column(JSONB, nullable=True)
    triggered_by      = Column(String(128))
    started_at        = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    ended_at          = Column(DateTime(timezone=True))
    result_summary    = Column(JSONB)

    parent_plan_run_id  = Column(Integer, ForeignKey("plan_run.id"), nullable=True)
    root_plan_run_id    = Column(Integer, ForeignKey("plan_run.id"), nullable=True)
    chain_index         = Column(Integer, nullable=False, default=0, server_default="0")
    next_plan_triggered = Column(Boolean, nullable=False, default=False, server_default="false")

    # ── ADR-0026 P1 step 1: admission-queue columns (schema only) ──
    # No code path writes these yet — they activate with the admission-queue
    # feature flag. Kept nullable / defaulted so the migration is additive.
    priority             = Column(Integer, nullable=False, default=0, server_default="0")
    queue_reason         = Column(String(32))   # DEVICE_BUSY / RESOURCE_BUSY / PRIORITY_WAIT / PRECHECK_STALE
    next_admission_at    = Column(DateTime(timezone=True))
    admission_token      = Column(String(64))   # idempotent admission token (pump)
    admission_attempt_id = Column(String(64))   # stale-PRECHECK ownership (reaper)
    precheck_started_at  = Column(DateTime(timezone=True))
    enqueued_at          = Column(DateTime(timezone=True))

    # ── ADR-0026 §6: O(1) terminal-aggregation counters ──
    # failed = failed_only semantics (excludes aborted), aligned with
    # plan_run_aggregation.py. Maintained by job_terminalization
    # (on_job_terminal / on_job_terminal_sync) + counter_reconcile sweep.
    total_job_count     = Column(Integer, nullable=False, default=0, server_default="0")
    terminal_job_count  = Column(Integer, nullable=False, default=0, server_default="0")
    completed_job_count = Column(Integer, nullable=False, default=0, server_default="0")
    failed_job_count    = Column(Integer, nullable=False, default=0, server_default="0")
    aborted_job_count   = Column(Integer, nullable=False, default=0, server_default="0")

    plan = relationship("Plan", foreign_keys=[plan_id],
                        back_populates="runs")
    jobs = relationship("backend.models.job.JobInstance",
                        back_populates="plan_run", lazy="dynamic")

    __table_args__ = (
        CheckConstraint(
            "failure_threshold >= 0.0 AND failure_threshold <= 1.0",
            name="ck_plan_run_failure_threshold",
        ),
        CheckConstraint(
            "run_type IN ('MANUAL','SCHEDULE','CHAIN')",
            name="ck_plan_run_type",
        ),
        Index("idx_plan_run_plan", "plan_id"),
        Index("idx_plan_run_status", "status"),
        Index("idx_plan_run_parent", "parent_plan_run_id"),
        Index("idx_plan_run_root", "root_plan_run_id"),
        Index(
            "uniq_plan_run_chain_child",
            "parent_plan_run_id",
            "plan_id",
            unique=True,
            postgresql_where=text("parent_plan_run_id IS NOT NULL"),
        ),
        # ADR-0026 P2-3: match pump ORDER BY priority DESC, enqueued_at ASC
        # (partial — QUEUED rows are few; next_admission_at is a filter, not sort).
        Index(
            "idx_plan_run_admission_queue",
            "priority",
            "enqueued_at",
            postgresql_ops={"priority": "DESC", "enqueued_at": "ASC"},
            postgresql_where=text("status = 'QUEUED'"),
        ),
    )


class PlanRunHost(Base):
    """ADR-0026: per-host projection of a PlanRun (prepare-time snapshot).

    Created together with :class:`PlanRunTargetDevice` at prepare as an
    immutable dispatch snapshot. Coordinator fields (admitted_at /
    coordinator_epoch / coordinator_heartbeat_at /
    admission_batch_size_snapshot) are enabled after admission
    (PRECHECK→RUNNING) and stay NULL/default while QUEUED.

    Created by ``prepare_plan_run`` and activated by the admission transaction.
    """
    __tablename__ = "plan_run_host"

    id           = Column(Integer, primary_key=True)
    # CASCADE (step 1.1): retention cleanup deletes PlanRun rows directly —
    # pure-snapshot children must go with them, at the DB level.
    plan_run_id  = Column(Integer, ForeignKey("plan_run.id", ondelete="CASCADE"), nullable=False)
    host_id      = Column(String(64), ForeignKey("host.id"), nullable=False)
    device_count = Column(Integer, nullable=False, default=0, server_default="0")
    # status expresses admission/liveness; phase expresses the business stage —
    # the two are orthogonal (ADR-0026 data-model section).
    status       = Column(String(32), nullable=False,
                          default="PENDING_ADMISSION", server_default="PENDING_ADMISSION")
    phase        = Column(String(32))  # INIT / PATROL / TEARDOWN / BARRIER_WAIT

    admitted_at              = Column(DateTime(timezone=True))
    coordinator_epoch        = Column(Integer, nullable=False, default=0, server_default="0")
    coordinator_heartbeat_at = Column(DateTime(timezone=True))
    # Audit-only snapshot of the OperationScheduler cap at admission time;
    # the LIVE limit is host/agent config (hot-adjustable, host-global).
    admission_batch_size_snapshot = Column(Integer)
    last_error   = Column(String(512))
    queue_reason = Column(String(32))

    # Host-scoped mirror of the PlanRun O(1) counters (barrier/phase judgement)
    total_job_count     = Column(Integer, nullable=False, default=0, server_default="0")
    terminal_job_count  = Column(Integer, nullable=False, default=0, server_default="0")
    completed_job_count = Column(Integer, nullable=False, default=0, server_default="0")
    failed_job_count    = Column(Integer, nullable=False, default=0, server_default="0")
    aborted_job_count   = Column(Integer, nullable=False, default=0, server_default="0")

    __table_args__ = (
        UniqueConstraint("plan_run_id", "host_id", name="uq_plan_run_host"),
        # FK-addressing target for PlanRunTargetDevice's composite consistency
        # FK (id alone is already unique; the pair exists for FK matching).
        UniqueConstraint("id", "plan_run_id", name="uq_plan_run_host_id_plan_run"),
        Index("idx_plan_run_host_host_phase", "host_id", "phase"),
    )


class PlanRunTargetDevice(Base):
    """ADR-0026: prepare-time relational snapshot of a PlanRun's target devices.

    Authoritative replacement for ``run_context.dispatch_device_ids`` JSON
    (which stays as a compatibility read path): supports the all-ready
    admission join, host grouping, device-migration audit via
    ``host_id_snapshot``, and 1000-device set queries.

    Created by ``prepare_plan_run`` as the immutable admission target snapshot.
    """
    __tablename__ = "plan_run_target_device"

    id               = Column(Integer, primary_key=True)
    plan_run_id      = Column(Integer, ForeignKey("plan_run.id", ondelete="CASCADE"), nullable=False)
    # No inline FK: bound to plan_run_host via the composite consistency FK in
    # __table_args__ (step 1.1), so a target row can never reference another
    # PlanRun's host-group row.
    plan_run_host_id = Column(Integer, nullable=False)
    device_id        = Column(Integer, ForeignKey("device.id"), nullable=False)
    # Host at prepare time; diverges from device.host_id if the device moved.
    host_id_snapshot = Column(String(64), nullable=False)
    sort_order       = Column(Integer, nullable=False, default=0, server_default="0")

    __table_args__ = (
        UniqueConstraint("plan_run_id", "device_id", name="uq_plan_run_target_device"),
        ForeignKeyConstraint(
            ["plan_run_host_id", "plan_run_id"],
            ["plan_run_host.id", "plan_run_host.plan_run_id"],
            ondelete="CASCADE",
        ),
        Index("idx_prtd_device", "device_id"),
        Index("idx_prtd_plan_run_host", "plan_run_host_id"),
        # ADR-0026 P2-3: ordered target-device scan at admission (sort_order).
        Index("idx_prtd_plan_run_sort", "plan_run_id", "sort_order"),
    )
