"""add secure ssh credential fields to host

Revision ID: l2m3n4o5p6q7
Revises: h1c2d3e4f5g6
Create Date: 2026-05-15
"""

from alembic import op
import sqlalchemy as sa


revision = "l2m3n4o5p6q7"
down_revision = "h1c2d3e4f5g6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("host", sa.Column("ssh_password_enc", sa.String(length=1024), nullable=True))
    op.add_column("host", sa.Column("ssh_known_hosts_path", sa.String(length=512), nullable=True))


def downgrade() -> None:
    op.drop_column("host", "ssh_known_hosts_path")
    op.drop_column("host", "ssh_password_enc")
