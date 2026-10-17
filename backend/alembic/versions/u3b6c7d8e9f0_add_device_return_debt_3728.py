"""add device_return_debt + device.return_state — 控制面欠账（ADR-0061 D5 / #3723 U3）

Revision ID: u3b6c7d8e9f0
Revises: c3d4e5f6a7b8
Create Date: 2026-10-17

ADR-0061 D5 / #3723 §3.4 U3：
- 新表 ``device_return_debt``（单数表名）：按（设备，专项，来源）持久化欠账条目。
- ``device.return_state``（JSONB）：按专项记录归还状态（returned / debt / unverified）。
部分唯一索引保证每个（device_id, family, source_kind, source_job_id）至多一条 open 条目。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "u3b6c7d8e9f0"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "device_return_debt",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "device_id",
            sa.Integer(),
            sa.ForeignKey("device.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("family", sa.String(length=64), nullable=False),
        sa.Column("source_kind", sa.String(length=32), nullable=False),
        sa.Column(
            "source_job_id",
            sa.Integer(),
            sa.ForeignKey("job_instance.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("source_fencing_token", sa.String(length=256), nullable=True),
        sa.Column("cause", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("closed_reason", sa.String(length=32), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("evidence", postgresql.JSONB(), nullable=True),
    )
    op.create_index(
        "ix_device_return_debt_device_state",
        "device_return_debt",
        ["device_id", "state"],
    )
    op.create_index(
        "ix_device_return_debt_family_state",
        "device_return_debt",
        ["family", "state"],
    )
    # Partial unique: at most one open entry per (device, family, source_kind, source_job_id).
    # COALESCE(source_job_id, 0) so stock/job-null sources still collide under uniqueness.
    op.execute(
        """
        CREATE UNIQUE INDEX uq_device_return_debt_open_source
        ON device_return_debt (
            device_id,
            family,
            source_kind,
            (COALESCE(source_job_id, 0))
        )
        WHERE state = 'open'
        """
    )
    op.add_column(
        "device",
        sa.Column("return_state", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("device", "return_state")
    op.execute("DROP INDEX IF EXISTS uq_device_return_debt_open_source")
    op.drop_index("ix_device_return_debt_family_state", table_name="device_return_debt")
    op.drop_index("ix_device_return_debt_device_state", table_name="device_return_debt")
    op.drop_table("device_return_debt")
