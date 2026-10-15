"""add job_instance.disarm_results — 解除结果落库（ADR-0061 D4 / #3723 U2）

Revision ID: c3d4e5f6a7b8
Revises: 9b7e24a6d081
Create Date: 2026-10-15

ADR-0061 D4 / #3723 §3.3 U2：为每个 job 按专项持久化解除结果六值，
不翻转 COMPLETED / FAILED / ABORTED 测试结论。列可空：存量行与未升级
Agent 的终态在推导前均为 NULL。

仅新增 ``job_instance.disarm_results``（JSONB，nullable）；无回填、无索引。
``downgrade`` 会丢弃已落库的解除结果。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c3d4e5f6a7b8"
down_revision = "9b7e24a6d081"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "job_instance",
        sa.Column("disarm_results", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("job_instance", "disarm_results")
