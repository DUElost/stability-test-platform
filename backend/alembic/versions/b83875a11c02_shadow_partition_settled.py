"""#3875: persist the irreversible shadow partition exit.

Revision ID: b83875a11c02
Revises: b8g4a719c203
"""

from alembic import op
import sqlalchemy as sa

revision = "b83875a11c02"
down_revision = "b8g4a719c203"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("partition_mode", sa.Column("shadow_settled_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("host", sa.Column("agent_artifact_digest_reported_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("host", "agent_artifact_digest_reported_at")
    op.drop_column("partition_mode", "shadow_settled_at")
