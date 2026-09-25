"""plan_run_pending_aggregation + plan_run.terminal_effects_state —— ADR-0052 D2/D4 (#3244)

Revision ID: d4e8f2a7c9b1
Revises: c7d2e5f8a1b3
Create Date: 2026-09-26

ADR-0052 D2：Job 终态事务只向 insert-only pending 表写一行标记（复合主键
``(plan_run_id, job_id)`` 兼去重键），父 Run 热行移出终态事务；D3/§7-2 消费即删
（聚合事务内删除）。ADR-0052 D4：``terminal_effects_state``（NULL/pending/done）
是终态副作用的重复执行保护——父终态**同事务**落 'pending'，编排完成后独立提交
置 'done'，补偿扫描只对 pending 重放。

down 仅删本表/本列，不触既有数据（派生计数无需回滚迁移——§6）。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d4e8f2a7c9b1"
down_revision = "c7d2e5f8a1b3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "plan_run_pending_aggregation",
        sa.Column("plan_run_id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["plan_run_id"], ["plan_run.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("plan_run_id", "job_id"),
    )
    op.create_index(
        "idx_prpa_run_created",
        "plan_run_pending_aggregation",
        ["plan_run_id", "created_at"],
    )
    op.add_column(
        "plan_run",
        sa.Column("terminal_effects_state", sa.String(length=16), nullable=True),
    )
    op.create_index(
        "idx_plan_run_terminal_effects_pending",
        "plan_run",
        ["id"],
        postgresql_where=sa.text("terminal_effects_state = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index(
        "idx_plan_run_terminal_effects_pending", table_name="plan_run"
    )
    op.drop_column("plan_run", "terminal_effects_state")
    op.drop_index("idx_prpa_run_created", table_name="plan_run_pending_aggregation")
    op.drop_table("plan_run_pending_aggregation")
