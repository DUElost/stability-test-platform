"""#3875: persist the irreversible shadow partition exit.

Revision ID: b83875a11c02
Revises: b8g1c4a7d2e9
"""

from alembic import op
import sqlalchemy as sa

revision = "b83875a11c02"
down_revision = "b8g1c4a7d2e9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("partition_mode", sa.Column("shadow_settled_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("partition_mode", "shadow_settled_at")
