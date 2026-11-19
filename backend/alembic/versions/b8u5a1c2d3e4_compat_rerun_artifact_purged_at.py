"""B8 U5 — ``compat_rerun.artifact_purged_at``（#4045 裁决 3 = 3a）。

派生族回收只删磁盘目录，**不删登记行**（审计事实与累计发生数可追溯入口）。为避免
每次 Dry-run 都把已回收项误报成「目录不存在/不安全」的异常，这里记下回收动作本身的
时间戳，让报告能把「候选 / 保留 / 已回收」分三栏呈现。纯 additive，存量行全为 NULL
（尚未发生过回收），downgrade 完整撤销。

Revision ID: b8u5a1c2d3e4
Revises:    b8u2h1c2a3p4
Create Date: 2026-11-19
"""

from alembic import op
import sqlalchemy as sa

revision = "b8u5a1c2d3e4"
down_revision = "b8u2h1c2a3p4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "compat_rerun",
        sa.Column("artifact_purged_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("compat_rerun", "artifact_purged_at")
