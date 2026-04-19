from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from backend.core.database import Base


class TaskTemplate(Base):
    __tablename__ = "task_template"

    id                     = Column(Integer, primary_key=True)
    workflow_definition_id = Column(Integer, ForeignKey("workflow_definition.id", ondelete="CASCADE"), nullable=False)
    name                   = Column(String(256), nullable=False)
    pipeline_def           = Column(JSONB, nullable=False)
    platform_filter        = Column(JSONB)
    sort_order             = Column(Integer, nullable=False, default=0)
    created_at             = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)

    definition = relationship(
        "backend.models.workflow.WorkflowDefinition",
        foreign_keys=[workflow_definition_id],
        back_populates="task_templates",
    )
    jobs = relationship("JobInstance", back_populates="task_template", lazy="dynamic")


class JobInstance(Base):
    __tablename__ = "job_instance"

    id               = Column(Integer, primary_key=True)
    workflow_run_id  = Column(Integer, ForeignKey("workflow_run.id"), nullable=False)
    task_template_id = Column(Integer, ForeignKey("task_template.id"), nullable=False)
    device_id        = Column(Integer, ForeignKey("device.id"), nullable=False)
    host_id          = Column(String(64), ForeignKey("host.id"))
    status           = Column(String(32), nullable=False, default="PENDING")
    status_reason    = Column(Text)
    pipeline_def     = Column(JSONB, nullable=False)
    started_at         = Column(DateTime(timezone=True))
    ended_at           = Column(DateTime(timezone=True))
    report_json        = Column(JSONB)
    jira_draft_json    = Column(JSONB)
    post_processed_at  = Column(DateTime(timezone=True))
    # ---- Watcher 生命周期回填（由 Agent 通过 complete.watcher_summary 写入）----
    # 字段来源见 backend/agent/watcher/contracts.py WatcherSummaryPayload
    watcher_started_at = Column(DateTime(timezone=True))
    watcher_stopped_at = Column(DateTime(timezone=True))
    watcher_capability = Column(String(32))   # inotifyd_root | inotifyd_shell | polling | unavailable | skipped | stub
    log_signal_count   = Column(Integer, nullable=False, default=0, server_default="0")
    created_at         = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at         = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    workflow_run   = relationship("backend.models.workflow.WorkflowRun", foreign_keys=[workflow_run_id], back_populates="jobs")
    task_template  = relationship("TaskTemplate", foreign_keys=[task_template_id], back_populates="jobs")
    device         = relationship("backend.models.host.Device", foreign_keys=[device_id])
    host           = relationship("backend.models.host.Host", foreign_keys=[host_id])
    step_traces    = relationship("StepTrace", back_populates="job", lazy="dynamic")
    artifacts      = relationship("JobArtifact", back_populates="job", lazy="dynamic")
    log_signals    = relationship("JobLogSignal", back_populates="job", lazy="dynamic")

    __table_args__ = (
        Index("idx_job_instance_status",   "status"),
        Index("idx_job_instance_workflow",  "workflow_run_id"),
        Index("idx_job_instance_host",      "host_id"),
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
    original_ts   = Column(DateTime(timezone=True), nullable=False)
    created_at    = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)

    job = relationship("JobInstance", foreign_keys=[job_id], back_populates="step_traces")

    __table_args__ = (
        UniqueConstraint("job_id", "step_id", "event_type", name="uq_step_trace_idempotent"),
        Index("idx_step_trace_job", "job_id"),
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
    created_at  = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)

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
    host_id        = Column(String(64), nullable=False)
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
    received_at    = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    extra          = Column(JSONB)

    job = relationship("JobInstance", foreign_keys=[job_id], back_populates="log_signals")

    __table_args__ = (
        UniqueConstraint("job_id", "seq_no", name="uq_job_log_signal_job_seq"),
        Index("idx_job_log_signal_job",       "job_id"),
        Index("idx_job_log_signal_category",  "job_id", "category"),
        Index("idx_job_log_signal_detected",  "detected_at"),
    )
