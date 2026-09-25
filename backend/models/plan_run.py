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
    plan_snapshot     = Column(JSONB, nullable=False)
    run_type          = Column(String(16), nullable=False)
    run_context       = Column(JSONB, nullable=True)
    triggered_by      = Column(String(128))
    # ADR-0029 归属（P1 M-a）：NULL = 迁移期瞬态，M-b 回填后归零（存量 run 归 Legacy）。
    # ADR-0029 v2.5：project_id 是派发时冻结的**悬空快照**（M4 删哨兵行时
    # 同步 drop FK——哨兵删除后旧 run 指向已删行，FK 会成为历史追溯的障碍；
    # 归属真源是 device.model ⋈ project_model，plan_run 快照只做归因）
    project_id        = Column(Integer, nullable=True)
    # 运行期属性（D3）：由 device.build_display_id（心跳上报）取值，版本不进项目。
    build_version     = Column(String(256), nullable=True)
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

    # ADR-0052 D4: duplicate-execution protection for parent-terminal side
    # effects (通知 / chain / dedup / 报告刷新). Written 'pending' in the SAME
    # transaction that finalizes the parent (``_finalize_plan_run``); the
    # orchestration flips it to 'done' after the side-effect block completes.
    # NULL = never finalized. Recovery replays the block only while 'pending'.
    terminal_effects_state = Column(String(16))

    plan = relationship("Plan", foreign_keys=[plan_id],
                        back_populates="runs")
    # ADR-0029：归属项目（project_id 快照语义——Plan 改归属不影响历史 Run）。
    # v2.5 M4 后 project_id 无 FK（悬空快照），显式 primaryjoin 供 ORM 使用。
    project = relationship(
        "TestProject",
        primaryjoin="PlanRun.project_id == TestProject.id",
        foreign_keys=[project_id],
    )
    jobs = relationship("backend.models.job.JobInstance",
                        back_populates="plan_run", lazy="dynamic")

    __table_args__ = (
        CheckConstraint(
            "run_type IN ('MANUAL','SCHEDULE','CHAIN')",
            name="ck_plan_run_type",
        ),
        Index("idx_plan_run_plan", "plan_id"),
        Index("idx_plan_run_status", "status"),
        Index("idx_plan_run_project", "project_id"),
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
        # ADR-0052 D4 补偿扫描只查 pending 行（终态已提交、副作用未收尾），
        # 其余 run 恒为 done/NULL——部分索引 keeps 它近乎零成本。
        Index(
            "idx_plan_run_terminal_effects_pending",
            "id",
            postgresql_where=text("terminal_effects_state = 'pending'"),
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
    host_id      = Column(String(64), ForeignKey("host.id", ondelete="CASCADE", onupdate="CASCADE"), nullable=False)
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


class PlanRunPendingAggregation(Base):
    """ADR-0052 D2 — durable aggregation trigger (insert-only pending marker).

    Job 终态事务**只插不改**本表的一行（``plan_run_id`` + ``job_id``，复合主键
    兼去重键——outbox 重放同终态时 ON CONFLICT DO NOTHING 幂等）；父 Run 热行
    不进入 Job 终态事务。聚合者按 ``plan_run_id`` 合并消费：读 Job 事实重算
    计数 + **同一事务**删除已消费行（§7-2 消费即删——pending 是工作队列不是事实，
    事实源是 ``job_instance``）。提交后 SAQ 唤醒（``key=agg:{plan_run_id}`` 去重）
    只是传输；唤醒丢失由 counter_reconciler 扫描本表重放。

    FK 均 ``ondelete=CASCADE``：retention 直接删 PlanRun 行时标记随行消失，
    不留孤儿触发。
    """
    __tablename__ = "plan_run_pending_aggregation"

    plan_run_id = Column(
        Integer, ForeignKey("plan_run.id", ondelete="CASCADE"), primary_key=True
    )
    # 无 FK：与 PlanRunTargetDevice 同一理由——job 行由其它表引用且删除路径独立，
    # 标记的语义只到「该 job_id 曾落终态待聚合」，聚合重算读的是 job 事实。
    job_id = Column(Integer, primary_key=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=text("now()"),
    )

    __table_args__ = (
        # 恢复扫描按消费顺序取（最老先聚合）。
        Index("idx_prpa_run_created", "plan_run_id", "created_at"),
    )
