"""ADR-0029 v2.5 D10 M3 — 删 device.project_id / project_pinned / match_type。

归属已全部派生（device.model ⋈ project_model），副本列无读者（M2 读路径
切换 + M3 写路径迁移完成）。project_model.match_type 收敛（仅 MODEL 值，
SERIAL 预留已放弃）。

Revision ID: a9b8c7d6e5f4
Revises: m3n4o5p6q7r8
Create Date: 2026-08-31
"""

from alembic import op
import sqlalchemy as sa

revision = "a9b8c7d6e5f4"
down_revision = "m3n4o5p6q7r8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("device", "project_id")
    op.drop_column("device", "project_pinned")
    op.drop_column("project_model", "match_type")


def downgrade() -> None:
    op.add_column("device", sa.Column("project_id", sa.Integer(), nullable=True))
    op.add_column("device", sa.Column("project_pinned", sa.Boolean(),
                                      nullable=False, server_default=sa.text("false")))
    op.add_column("project_model", sa.Column("match_type", sa.String(16),
                                             nullable=False, server_default="MODEL"))
