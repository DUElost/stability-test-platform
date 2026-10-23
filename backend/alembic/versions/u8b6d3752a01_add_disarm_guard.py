"""Persist bounded disarm guard (ADR-0061 v1.1 / B6 U8)."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "u8b6d3752a01"
down_revision = "u3b6c7d8e9f0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("job_instance", sa.Column("disarm_guard", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("job_instance", "disarm_guard")
