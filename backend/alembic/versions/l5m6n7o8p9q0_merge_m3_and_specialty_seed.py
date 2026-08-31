"""Merge heads: a9b8c7d6e5f4 (M3 drop columns) + k2l3m4n5o6p7 (specialty seed).

同挂 m3n4o5p6q7r8——无数据依赖，纯合并。

Revision ID: l5m6n7o8p9q0
Revises: a9b8c7d6e5f4, k2l3m4n5o6p7
Create Date: 2026-08-31
"""

from alembic import op

revision = "l5m6n7o8p9q0"
down_revision = ("a9b8c7d6e5f4", "k2l3m4n5o6p7")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
