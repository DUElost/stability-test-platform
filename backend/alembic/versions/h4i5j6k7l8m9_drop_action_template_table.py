"""drop unused action_template table — #1890

Revision ID: h4i5j6k7l8m9
Revises: f3a4b5c6d7e8
Create Date: 2026-09-13

``action_template`` 由 ``f4a5b6c7d8e9.upgrade()`` 创建后，ORM/路由已在
#734/#1526 拆除，但 forward 链从未 ``drop_table``（仅 downgrade 有 drop，
曾被误判为「表级删除已完成」）。本 revision 在 ``upgrade()`` 真正删除表与
索引；``g7h8i9j0k1l2`` 的 ``CREATE INDEX IF NOT EXISTS ... ON action_template``
排在本 revision **之前**，空库 upgrade head 仍先建后删，不会报错。

幂等：表/索引已不存在时跳过（兼容曾手工清理的环境）。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "h4i5j6k7l8m9"
down_revision = "f3a4b5c6d7e8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = set(inspector.get_table_names())
    if "action_template" not in tables:
        return
    indexes = {idx["name"] for idx in inspector.get_indexes("action_template")}
    if "ix_action_template_active" in indexes:
        op.drop_index("ix_action_template_active", table_name="action_template")
    op.drop_table("action_template")


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if "action_template" in inspector.get_table_names():
        return
    op.create_table(
        "action_template",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False, unique=True),
        sa.Column("description", sa.Text()),
        sa.Column("action", sa.String(256), nullable=False),
        sa.Column("version", sa.String(64)),
        sa.Column(
            "params",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "timeout_seconds",
            sa.Integer(),
            nullable=False,
            server_default="300",
        ),
        sa.Column(
            "retry",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_action_template_active",
        "action_template",
        ["is_active", "name"],
    )
