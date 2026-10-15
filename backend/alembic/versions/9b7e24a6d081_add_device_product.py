"""Preserve the ADB product independently of the device model.

Revision ID: 9b7e24a6d081
Revises: b2c3d4e5f6a7
"""

from alembic import op
import sqlalchemy as sa

revision = "9b7e24a6d081"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("device", sa.Column("product", sa.String(128), nullable=True))


def downgrade() -> None:
    op.drop_column("device", "product")
