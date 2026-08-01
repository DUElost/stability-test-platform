"""plan.barrier_timeout_seconds — 让 INIT→PATROL barrier 预算随计划可配

Revision ID: i4j5k6l7m8n9
Revises: h3i4j5k6l7m8
Create Date: 2026-08-01

固定 600s 只能覆盖「单设备 init ≤ 2.5 min」的场景。barrier 里只有**先到者**在等
（最后到达者直接放行），所以预算要覆盖的是同一 host 上最快与最慢设备的 init
**落差**；而 init 又被 OperationScheduler 的 permit cap 串行化，于是

    required ≈ (ceil(N / C) − 1) × T      # N=host 设备数, C=permit cap, T=单设备 init 耗时

当前最坏 host 有 23 台设备、C=5 ⇒ 系数 4。即将接入的自动刷机 T 在分钟到小时量级，
600s 会让**先做完 init 的设备被慢同伴连坐失败**（barrier 超时 → 跳过 patrol → 终止）。

NULL = 沿用 STP_BARRIER_TIMEOUT_SECONDS / 600s，既有 Plan 行为不变。
"""

from alembic import op
import sqlalchemy as sa

revision = "i4j5k6l7m8n9"
down_revision = "h3i4j5k6l7m8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "plan",
        sa.Column("barrier_timeout_seconds", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("plan", "barrier_timeout_seconds")
