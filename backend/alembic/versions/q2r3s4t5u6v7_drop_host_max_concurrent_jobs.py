"""drop host max_concurrent_jobs column

Revision ID: q2r3s4t5u6v7
Revises: p1q2r3s4t5u6
Create Date: 2026-06-12
"""

from alembic import op
import sqlalchemy as sa


revision = "q2r3s4t5u6v7"
down_revision = "p1q2r3s4t5u6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 93b9935 移除 host 槽位上限后该列已无任何代码消费方;
    # 真实并发上限 = min(Agent 上报 capacity, 空闲健康设备数)。
    op.drop_column("host", "max_concurrent_jobs")


def downgrade() -> None:
    # 先带 server_default 回填存量行(原 ORM default=2),再移除 server_default,
    # 与 s7t8u9v0w1x2 初始添加该列时的语义一致。
    op.add_column(
        "host",
        sa.Column(
            "max_concurrent_jobs",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("2"),
        ),
    )
    op.alter_column("host", "max_concurrent_jobs", server_default=None)
