"""Add script catalog and workflow setup fields.

Revision ID: o3i4j5k6l7m8
Revises:    n2h3i4j5k6l7
Create Date: 2026-04-25
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "o3i4j5k6l7m8"
down_revision = "n2h3i4j5k6l7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "script",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("display_name", sa.String(length=256), nullable=True),
        sa.Column("category", sa.String(length=64), nullable=True),
        sa.Column("script_type", sa.String(length=16), nullable=False),
        sa.Column("version", sa.String(length=32), nullable=False),
        sa.Column("nfs_path", sa.Text(), nullable=False),
        sa.Column("entry_point", sa.String(length=256), nullable=True),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("param_schema", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("name", "version", name="uq_script_name_version"),
    )
    op.create_index("idx_script_active_name", "script", ["is_active", "name"])
    op.create_index("idx_script_category", "script", ["category"])

    op.add_column(
        "workflow_definition",
        sa.Column("setup_pipeline", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "workflow_definition",
        sa.Column("teardown_pipeline", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "host",
        sa.Column("script_catalog_version", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("host", "script_catalog_version")
    op.drop_column("workflow_definition", "teardown_pipeline")
    op.drop_column("workflow_definition", "setup_pipeline")
    op.drop_index("idx_script_category", table_name="script")
    op.drop_index("idx_script_active_name", table_name="script")
    op.drop_table("script")
