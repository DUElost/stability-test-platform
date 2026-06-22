"""Add auto_archive_interval_seconds to plan table."""
from alembic import op
import sqlalchemy as sa

revision = "t5u6v7w8x9y0"
down_revision = "s4t5u6v7w8x9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "plan",
        sa.Column("auto_archive_interval_seconds", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("plan", "auto_archive_interval_seconds")
