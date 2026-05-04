"""Drop tool table and host.tool_catalog_version column

Revision ID: 5790a8de0a87
Revises: 219f2f131538
Create Date: 2026-05-05 01:34:23.147560

The tool:<id> action format is deprecated; all execution paths use script:<name>.
- Drop the tool table (Postgres)
- Drop tool_catalog_version column from host
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '5790a8de0a87'
down_revision: Union[str, None] = '219f2f131538'
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


def _col_exists(conn, table_name: str, col_name: str) -> bool:
    result = conn.execute(
        sa.text(
            "SELECT EXISTS ("
            "  SELECT 1 FROM information_schema.columns "
            "  WHERE table_schema = 'public' AND table_name = :tbl AND column_name = :col"
            ")"
        ),
        {"tbl": table_name, "col": col_name},
    )
    return result.scalar()


def upgrade() -> None:
    conn = op.get_bind()
    if _col_exists(conn, "host", "tool_catalog_version"):
        op.drop_column("host", "tool_catalog_version")
    if _table_exists(conn, "tool"):
        op.drop_table("tool")


def downgrade() -> None:
    # Re-add tool_catalog_version column (no data recovery)
    conn = op.get_bind()
    if not _col_exists(conn, "host", "tool_catalog_version"):
        op.add_column("host", sa.Column("tool_catalog_version", sa.String(64)))
    if not _table_exists(conn, "tool"):
        op.create_table(
            "tool",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(128), nullable=False),
            sa.Column("version", sa.String(32), nullable=False),
            sa.Column("script_path", sa.Text(), nullable=False),
            sa.Column("script_class", sa.String(128), nullable=False),
            sa.Column("param_schema", sa.dialects.postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("description", sa.Text()),
            sa.Column("category", sa.String(64)),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("name", "version", name="uq_tool_name_version"),
        )
