"""add users.token_version — R02-D2 会话纪元（#902）

Revision ID: n4o5p6q7r8s9
Revises: k1l2m3n4o5p6
Create Date: 2026-09-08

users.token_version INTEGER NOT NULL DEFAULT 1：改密/重置/停用/改角色时
递增（应用层），携带旧 ``ver`` claim 的在发 token 立即失效。带常量
server_default 的 ADD COLUMN 在 PG 11+ 为元数据级变更，不重写存量行。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "n4o5p6q7r8s9"
down_revision = "k1l2m3n4o5p6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("token_version", sa.Integer(), nullable=False, server_default="1"),
    )


def downgrade() -> None:
    op.drop_column("users", "token_version")
