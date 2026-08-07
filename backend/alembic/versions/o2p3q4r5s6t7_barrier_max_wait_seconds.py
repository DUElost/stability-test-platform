"""plan.barrier_max_wait_seconds — progress-aware barrier 绝对硬顶（#174）。

Revision ID: o2p3q4r5s6t7
Revises: n1o2p3q4r5s6
Create Date: 2026-08-06

#174：progress-aware barrier 的续期逻辑（#117/#148）无总等待硬顶——只要 peer
持续被判为「活」（假 PROGRESS / WAITING_EXECUTION_SLOT 僵死），先到达者可无限
拉长等待。新增 Plan 直列字段 barrier_max_wait_seconds：从首次进入
_await_phase_barrier 起算的绝对上限（NULL = 保持现行为，不设硬顶）。
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "o2p3q4r5s6t7"
down_revision = "n1o2p3q4r5s6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {c["name"] for c in inspect(bind).get_columns("plan")}
    if "barrier_max_wait_seconds" not in columns:
        op.add_column(
            "plan",
            sa.Column("barrier_max_wait_seconds", sa.Integer(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    columns = {c["name"] for c in inspect(bind).get_columns("plan")}
    if "barrier_max_wait_seconds" in columns:
        op.drop_column("plan", "barrier_max_wait_seconds")
