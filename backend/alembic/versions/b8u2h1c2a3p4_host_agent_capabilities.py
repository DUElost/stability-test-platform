"""#4044: persist the Agent's current-heartbeat capability evidence.

Additive host columns only. NULL means "never reported"; an empty JSONB
array is the authoritative "current heartbeat reports no capabilities".

Revision ID: b8u2h1c2a3p4
Revises:    b8g8c1d2e3f4
Create Date: 2026-11-18
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "b8u2h1c2a3p4"
down_revision = "b8g8c1d2e3f4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "host",
        sa.Column("agent_capabilities", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "host",
        sa.Column(
            "agent_capabilities_reported_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "host",
        sa.Column("agent_capabilities_instance_id", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("host", "agent_capabilities_instance_id")
    op.drop_column("host", "agent_capabilities_reported_at")
    op.drop_column("host", "agent_capabilities")
