"""add revoked_refresh_token blacklist table

Revision ID: f1a2b3c4d5e6
Revises: n3o4p5q6r7s8
Create Date: 2026-05-21 00:00:00.000000

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "f1a2b3c4d5e6"
down_revision = "n3o4p5q6r7s8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "revoked_refresh_token",
        sa.Column("jti", sa.String(length=64), primary_key=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "idx_revoked_refresh_token_expires_at",
        "revoked_refresh_token",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_revoked_refresh_token_expires_at",
        table_name="revoked_refresh_token",
    )
    op.drop_table("revoked_refresh_token")
