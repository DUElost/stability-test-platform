from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import BigInteger, Column, DateTime, Enum as SAEnum, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from backend.core.database import Base
from backend.models.enums import JobStatus

JOB_STATUS_DB_ENUM = SAEnum(
    *(status.value for status in JobStatus),
    name="job_status",
    validate_strings=True,
)


class JobInstance(Base):
    __tablename__ = "job_instance"

    id               = Column(Integer, primary_key=True)
    plan_run_id      = Column(Integer, ForeignKey("plan_run.id"), nullable=False)
    plan_id          = Column(Integer, ForeignKey("plan.id"), nullable=False)
    device_id        = Column(Integer, ForeignKey("device.id"), nullable=False)
    host_id          = Column(String(64), ForeignKey("host.id"))
    status           = Column(JOB_STATUS_DB_ENUM, nullable=False, default=JobStatus.PENDING.value)
    status_reason    = Column(Text)
    pipeline_def     = Column(JSONB, nullable=False)
    started_at         = Column(DateTime(timezone=True))
    ended_at           = Column(DateTime(timezone=True))
    report_json        = Column(JSONB)
    jira_draft_json    = Column(JSONB)
    terminal_payload_digest = Column(String(64))
    post_processed_at  = Column(DateTime(timezone=True))
    watcher_started_at = Column(DateTime(timezone=True))
    watcher_stopped_at = Column(DateTime(timezone=True))
    watcher_capability = Column(String(32))
    log_signal_count   = Column(Integer, nullable=False, default=0, server_default="0")

    # ── ADR-0022: patrol heartbeat aggregation ──
    # 取代每周期 per-step step_trace 的写入；patrol 成功 step 不再写 trace,
    # 改由 Agent 周期性调用 POST /agent/jobs/{id}/patrol-heartbeat 累积统计。
    patrol_cycle_count          = Column(Integer, nullable=False, default=0, server_default="0")
    patrol_success_cycle_count  = Column(Integer, nullable=False, default=0, server_default="0")
    patrol_failed_cycle_count   = Column(Integer, nullable=False, default=0, server_default="0")
    current_patrol_step         = Column(Text)
    last_patrol_heartbeat_at    = Column(DateTime(timezone=True))
    # 退避: streak 累计连续失败次数; next_retry_at 非空表示退避中
    current_failure_streak      = Column(Integer, nullable=False, default=0, server_default="0")
    next_retry_at               = Column(DateTime(timezone=True))
    # 手动干预: NULL / RETRY_NOW / EXIT_REQUESTED ; Agent 下一周期检查后清零或响应
    manual_action               = Column(String(32))

    # ── ADR-0026 P1 step 1: 三个独立存活信号(不变量③,schema only) ──
    # 目前无代码路径写入;批量续租请求已前向兼容承载(agent_api.py
    # _ExtendBatchItemIn.execution_state / progress_marker),feature flag
    # 落地后回填。在此之前 recycler 仍以 updated_at 为唯一判据。
    execution_state             = Column(String(32))  # WAITING_EXECUTION_SLOT / EXECUTING_STEP / PATROL_SLEEP / WAITING_BARRIER
    last_execution_heartbeat_at = Column(DateTime(timezone=True))  # 执行器存活(EXECUTING_STEP 超时判据)
    last_progress_at            = Column(DateTime(timezone=True))  # 业务进度(非 patrol step 的进度证明)

    created_at         = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at         = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    plan_run       = relationship("backend.models.plan_run.PlanRun", foreign_keys=[plan_run_id], back_populates="jobs")
    plan           = relationship("backend.models.plan.Plan", foreign_keys=[plan_id])
    device         = relationship("backend.models.host.Device", foreign_keys=[device_id])
    host           = relationship("backend.models.host.Host", foreign_keys=[host_id])
    step_traces    = relationship("StepTrace", back_populates="job", lazy="dynamic")
    artifacts      = relationship("JobArtifact", back_populates="job", lazy="dynamic")
    log_signals    = relationship("JobLogSignal", back_populates="job", lazy="dynamic")

    __table_args__ = (
        Index("idx_job_instance_status",   "status"),
        Index("idx_job_instance_plan_run_status", "plan_run_id", "status"),
        Index("idx_job_instance_host",      "host_id"),
        UniqueConstraint(
            "plan_run_id", "device_id",
            name="uq_job_instance_plan_run_device",
        ),
        Index(
            "uq_job_active_per_device",
            "device_id",
            unique=True,
            postgresql_where=text(
                "status IN ('PENDING', 'RUNNING', 'UNKNOWN')"
            ),
        ),
        # ADR-0022: stall 检测 + per-PlanRun 设备矩阵聚合
        Index("idx_job_instance_patrol_heartbeat", "plan_run_id", "last_patrol_heartbeat_at"),
    )


class StepTrace(Base):
    __tablename__ = "step_trace"

    id            = Column(Integer, primary_key=True)
    job_id        = Column(Integer, ForeignKey("job_instance.id"), nullable=False)
    step_id       = Column(String(128), nullable=False)
    stage         = Column(String(32), nullable=False)
    status        = Column(String(32), nullable=False)
    event_type    = Column(String(32), nullable=False)
    output        = Column(Text)
    error_message = Column(Text)
    trace_event_id = Column(
        String(256), nullable=False, default=lambda: uuid4().hex,
    )
    original_ts   = Column(DateTime(timezone=True), nullable=False)
    created_at    = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    job = relationship("JobInstance", foreign_keys=[job_id], back_populates="step_traces")

    __table_args__ = (
        UniqueConstraint("trace_event_id", name="uq_step_trace_event_id"),
        Index("idx_step_trace_job", "job_id"),
        # ADR-0021/ADR-0022 C5a₂: timeline 端点按 (job_id, stage) GROUP BY 聚合
        Index("idx_step_trace_job_stage", "job_id", "stage"),
        # events 端点按 (job_id, status, original_ts) 扫描失败步骤并按时间排序
        Index("idx_step_trace_job_status_ts", "job_id", "status", "original_ts"),
    )


class JobArtifact(Base):
    """Artifact produced by a JobInstance (logs, reports, crash dumps).

    ADR-0018 5B2：由 Agent watcher LogPuller 成功产出的实文件经独立端点
        POST /api/v1/agent/jobs/{job_id}/artifacts 入库，
    首期只接受白名单 artifact_type（aee_crash / vendor_aee_crash / bugreport）。

    幂等键：(job_id, storage_uri) —— Agent 重试时的后端幂等保护。
    与 JobLogSignal 解耦：log_signal.artifact_uri 保留为原始指针；JobArtifact 是
        独立展示/下载入口，不反向成为 signal 权威源。
    """
    __tablename__ = "job_artifact"

    id          = Column(Integer, primary_key=True)
    job_id      = Column(Integer, ForeignKey("job_instance.id"), nullable=False)
    storage_uri = Column(String(512), nullable=False)
    artifact_type = Column(String(64), nullable=False, default="log")
    size_bytes  = Column(BigInteger)
    checksum    = Column(String(128))
    # ── 溯源字段（5B2，nullable）—— 便于与 log_signal JOIN 审计 ──
    source_category       = Column(String(32))    # AEE | VENDOR_AEE | BUGREPORT
    source_path_on_device = Column(String(512))   # 设备侧原路径
    created_at  = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    job = relationship("JobInstance", foreign_keys=[job_id], back_populates="artifacts")

    __table_args__ = (
        UniqueConstraint("job_id", "storage_uri", name="uq_job_artifact_job_storage"),
        Index("idx_job_artifact_job", "job_id"),
    )


class JobLogSignal(Base):
    """Watcher 采集的异常事件（ANR/AEE/VENDOR_AEE/MOBILELOG 等）的权威存储。

    字段契约见 backend/agent/watcher/contracts.py LogSignalEnvelope。
    幂等键：(job_id, seq_no) —— OutboxDrainer 按此键去重，重复 POST 不重复插入。
    Agent 通过 POST /api/v1/agent/log-signals 批量上送。
    """
    __tablename__ = "job_log_signal"

    id             = Column(BigInteger, primary_key=True)
    job_id         = Column(Integer, ForeignKey("job_instance.id", ondelete="CASCADE"), nullable=False)
    host_id        = Column(String(64), ForeignKey("host.id", ondelete="CASCADE"), nullable=False)
    device_serial  = Column(String(128), nullable=False)
    seq_no         = Column(BigInteger, nullable=False)
    category       = Column(String(32), nullable=False)   # ANR | AEE | VENDOR_AEE | MOBILELOG
    source         = Column(String(16), nullable=False)   # inotifyd | polling | logcat
    path_on_device = Column(String(512), nullable=False)
    artifact_uri   = Column(String(512))
    sha256         = Column(String(64))
    size_bytes     = Column(BigInteger)
    first_lines    = Column(Text)
    detected_at    = Column(DateTime(timezone=True), nullable=False)
    received_at    = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    extra          = Column(JSONB)

    job = relationship("JobInstance", foreign_keys=[job_id], back_populates="log_signals")

    __table_args__ = (
        UniqueConstraint("job_id", "seq_no", name="uq_job_log_signal_job_seq"),
        Index("idx_job_log_signal_job",       "job_id"),
        Index("idx_job_log_signal_category",  "job_id", "category"),
        Index("idx_job_log_signal_detected",  "detected_at"),
    )
