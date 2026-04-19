"""ADR-0018 5B2 — JobArtifact 幂等键 + watcher 溯源字段。

目的（严格收敛在 5B2 边界内）：
    1. 给 job_artifact 加 UniqueConstraint(job_id, storage_uri)
       —— 作为 Agent 重试时的后端幂等键，避免同一 NFS 文件在 job 维度重复入库
    2. 加两列 nullable 溯源字段（便于审计追溯 log_signal，不污染现有下载逻辑）
       - source_category       str(32)   AEE | VENDOR_AEE | BUGREPORT …（首期白名单）
       - source_path_on_device str(512)  设备侧原路径，便于与 log_signal.path_on_device JOIN

注意：
    - 不把 artifact_type 改为 enum（保持 str，首期在端点白名单，后期稳态后可迁）
    - log_signal.artifact_uri 继续保留；JobArtifact 只是独立展示/下载入口，不反过来成为 signal 权威源
    - 不增加 checksum 唯一约束：同一文件跨 job 允许重复入库；幂等只到 job 维度

Revision ID: n2h3i4j5k6l7
Revises:    m1g2h3i4j5k6
Create Date: 2026-04-19
"""
from alembic import op
import sqlalchemy as sa

revision = "n2h3i4j5k6l7"
down_revision = "m1g2h3i4j5k6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "job_artifact",
        sa.Column("source_category", sa.String(32), nullable=True),
    )
    op.add_column(
        "job_artifact",
        sa.Column("source_path_on_device", sa.String(512), nullable=True),
    )
    op.create_unique_constraint(
        "uq_job_artifact_job_storage",
        "job_artifact",
        ["job_id", "storage_uri"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_job_artifact_job_storage", "job_artifact", type_="unique",
    )
    op.drop_column("job_artifact", "source_path_on_device")
    op.drop_column("job_artifact", "source_category")
