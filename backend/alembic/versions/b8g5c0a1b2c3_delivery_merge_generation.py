"""B8 G5: monotonic merge generation and delivery basis rounds.

Revision ID: b8g5c0a1b2c3
Revises: b83875a11c02
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "b8g5c0a1b2c3"
down_revision = "b83875a11c02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "scan_round",
        sa.Column("merge_generation", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "delivery_version",
        sa.Column(
            "basis_rounds",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("delivery_version", "basis_rounds")
    op.drop_column("scan_round", "merge_generation")
