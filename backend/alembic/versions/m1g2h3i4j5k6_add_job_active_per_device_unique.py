"""Add partial unique index uq_job_active_per_device (同设备单活跃 Job 硬约束).

⚠️  本迁移是 watcher 铺路的"最后一步"，执行前强制检查：
    - **必须完成** backend/alembic/versions/k9f0a1b2c3d4_PREFLIGHT.md §1.1 脏数据扫描
    - **必须完成** §1.2 悬挂锁清理（否则 CREATE UNIQUE INDEX 会失败）
    - **watcher MVP PR 上线前**执行本迁移；治理 PR 阶段不得推进

为何拆独立 migration:
    k9f0a1b2c3d4 的列 + 表添加是平凡操作；本 partial unique index 在存在脏数据时
    会让 CREATE UNIQUE INDEX 失败，schema 处于中间态。拆开后可以让治理 PR 安全
    upgrade 前一个 migration，本 migration 留给 watcher MVP 阶段单独把关。

Revision ID: m1g2h3i4j5k6
Revises: k9f0a1b2c3d4
Create Date: 2026-04-18
"""
from alembic import op

revision = "m1g2h3i4j5k6"
down_revision = "k9f0a1b2c3d4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 同设备最多一个活跃 Job 的硬约束 — partial unique index
    # 活跃状态: PENDING / RUNNING / UNKNOWN
    # 执行前必读 PREFLIGHT.md §1.1 —— 任何脏数据都会让本语句失败
    op.execute(
        """
        CREATE UNIQUE INDEX uq_job_active_per_device
            ON job_instance (device_id)
         WHERE status IN ('PENDING', 'RUNNING', 'UNKNOWN')
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_job_active_per_device")
