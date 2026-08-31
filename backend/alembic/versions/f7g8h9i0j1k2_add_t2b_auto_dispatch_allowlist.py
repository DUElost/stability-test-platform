# -*- coding: utf-8 -*-
"""ai_assistant_config.t2b_auto_dispatch_allowlist（ADR-0031 附录 PR-D）

Revision ID: f7g8h9i0j1k2
Revises: f8a9b0c1d2e3
Create Date: 2026-08-31
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "f7g8h9i0j1k2"
down_revision = "f8a9b0c1d2e3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ai_assistant_config",
        sa.Column(
            "t2b_auto_dispatch_allowlist",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
    )


def downgrade() -> None:
    op.drop_column("ai_assistant_config", "t2b_auto_dispatch_allowlist")
