"""Add build_display_id to device

Revision ID: j8e9f0a1b2c3
Revises: i7d8e9f0a1b2
Create Date: 2026-04-11
"""
from alembic import op
import sqlalchemy as sa

revision = "j8e9f0a1b2c3"
down_revision = "i7d8e9f0a1b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("device", sa.Column("build_display_id", sa.String(256), nullable=True))


def downgrade() -> None:
    op.drop_column("device", "build_display_id")
