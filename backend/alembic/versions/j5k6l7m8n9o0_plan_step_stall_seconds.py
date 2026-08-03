"""plan_step.stall_seconds — 步骤停滞钟（#115 阶段 1）

PlanStep 配 stall_seconds 后，引擎按「多久无 PROGRESS 戳」判卡死并杀进程树。
NULL/0 = 关闭（缺省）。与 timeout_seconds 不同，0 是**合法且有意义的**
（= 不启用），所以 API/schema 侧 minimum 是 0 而不是 1。

Revision ID: j5k6l7m8n9o0
Revises: i4j5k6l7m8n9
Create Date: 2026-08-02
"""

from alembic import op
import sqlalchemy as sa

revision = "j5k6l7m8n9o0"
down_revision = "i4j5k6l7m8n9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "plan_step",
        sa.Column("stall_seconds", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("plan_step", "stall_seconds")
