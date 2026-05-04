"""Create tool table if missing

Revision ID: 219f2f131538
Revises: v0w1x2y3z4a5
Create Date: 2026-05-05 01:15:16.578737

The canonical migration a1b2c3d4e5f6 creates the tool table conditionally
(_table_exists guard). If it was skipped or dropped, recreate the full schema
including columns added by later migrations (category from g5b6c7d8e9f0).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = '219f2f131538'
down_revision: Union[str, None] = 'v0w1x2y3z4a5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(conn, table_name: str) -> bool:
    result = conn.execute(
        sa.text(
            "SELECT EXISTS ("
            "  SELECT 1 FROM information_schema.tables "
            "  WHERE table_schema = 'public' AND table_name = :tbl"
            ")"
        ),
        {"tbl": table_name},
    )
    return result.scalar()


def upgrade() -> None:
    conn = op.get_bind()
    if _table_exists(conn, "tool"):
        return

    op.create_table(
        "tool",
        sa.Column("id",           sa.Integer(),      primary_key=True),
        sa.Column("name",         sa.String(128),    nullable=False),
        sa.Column("version",      sa.String(32),     nullable=False),
        sa.Column("script_path",  sa.Text(),         nullable=False),
        sa.Column("script_class", sa.String(128),    nullable=False),
        sa.Column("param_schema", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("is_active",    sa.Boolean(),      nullable=False, server_default=sa.text("true")),
        sa.Column("description",  sa.Text()),
        sa.Column("category",     sa.String(64),     nullable=True),
        sa.Column("created_at",   sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at",   sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("name", "version", name="uq_tool_name_version"),
    )


def downgrade() -> None:
    # No-op: don't drop a table that may have been created by a1b2c3d4e5f6
    pass
