"""Add Device Log Watcher lifecycle fields + log signal storage.

⚠️  执行前必读: backend/alembic/versions/k9f0a1b2c3d4_PREFLIGHT.md
    - §1.2 悬挂锁清理（诊断项，非阻断）
    - ORM 同步必须与本迁移同 PR（§2）
    - 回滚方案见 §4

Changes:
  1. workflow_definition.watcher_policy (JSONB, nullable)
  2. job_instance: watcher_started_at / watcher_stopped_at / watcher_capability / log_signal_count
  3. job_log_signal: new authoritative table for agent-emitted log signals

注意：同设备单活跃 Job 的 partial unique index (uq_job_active_per_device) 已拆到
    独立迁移 m1g2h3i4j5k6_add_job_active_per_device_unique，需在 watcher MVP 上线前
    执行 PREFLIGHT §1.1 后手动推进。

Revision ID: k9f0a1b2c3d4
Revises: j8e9f0a1b2c3
Create Date: 2026-04-18
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "k9f0a1b2c3d4"
down_revision = "j8e9f0a1b2c3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. WorkflowDefinition.watcher_policy — 运行时可覆盖 Agent 默认策略
    op.add_column(
        "workflow_definition",
        sa.Column("watcher_policy", postgresql.JSONB, nullable=True),
    )

    # 2. JobInstance — Watcher 生命周期回填字段
    op.add_column("job_instance", sa.Column("watcher_started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("job_instance", sa.Column("watcher_stopped_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("job_instance", sa.Column("watcher_capability",  sa.String(32), nullable=True))
    op.add_column(
        "job_instance",
        sa.Column("log_signal_count", sa.Integer(), nullable=False, server_default="0"),
    )

    # 3. job_log_signal — 后端权威存储；Agent outbox 同步目的地
    op.create_table(
        "job_log_signal",
        sa.Column("id",              sa.BigInteger, primary_key=True),
        sa.Column("job_id",          sa.Integer, sa.ForeignKey("job_instance.id", ondelete="CASCADE"), nullable=False),
        sa.Column("host_id",         sa.String(64), nullable=False),
        sa.Column("device_serial",   sa.String(128), nullable=False),
        sa.Column("seq_no",          sa.BigInteger, nullable=False),
        sa.Column("category",        sa.String(32), nullable=False),
        sa.Column("source",          sa.String(16), nullable=False),  # inotifyd | polling | logcat
        sa.Column("path_on_device",  sa.String(512), nullable=False),
        sa.Column("artifact_uri",    sa.String(512), nullable=True),
        sa.Column("sha256",          sa.String(64),  nullable=True),
        sa.Column("size_bytes",      sa.BigInteger,  nullable=True),
        sa.Column("first_lines",     sa.Text,        nullable=True),
        sa.Column("detected_at",     sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at",     sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("extra",           postgresql.JSONB, nullable=True),
        sa.UniqueConstraint("job_id", "seq_no", name="uq_job_log_signal_job_seq"),
    )
    op.create_index("idx_job_log_signal_job",       "job_log_signal", ["job_id"])
    op.create_index("idx_job_log_signal_category",  "job_log_signal", ["job_id", "category"])
    op.create_index("idx_job_log_signal_detected",  "job_log_signal", ["detected_at"])


def downgrade() -> None:
    op.drop_index("idx_job_log_signal_detected",  table_name="job_log_signal")
    op.drop_index("idx_job_log_signal_category",  table_name="job_log_signal")
    op.drop_index("idx_job_log_signal_job",       table_name="job_log_signal")
    op.drop_table("job_log_signal")

    op.drop_column("job_instance", "log_signal_count")
    op.drop_column("job_instance", "watcher_capability")
    op.drop_column("job_instance", "watcher_stopped_at")
    op.drop_column("job_instance", "watcher_started_at")

    op.drop_column("workflow_definition", "watcher_policy")
