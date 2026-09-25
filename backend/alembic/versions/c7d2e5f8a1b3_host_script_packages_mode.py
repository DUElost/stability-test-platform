"""host.script_packages_mode —— #3222 fleet 包模式持续可核验

Revision ID: c7d2e5f8a1b3
Revises: ad51c1d3f2a1
Create Date: 2026-09-25

显式列（禁 host.extra 裸键，先例 ADR-0040 D2 / #2488）。presence sweep 每轮从
verify ack 的 package_active 推导写入；NULL = unknown（新装未 sweep / 无包身份目标）。
down 仅删本列。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c7d2e5f8a1b3"
down_revision = "ad51c1d3f2a1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("host", sa.Column("script_packages_mode", sa.String(length=8), nullable=True))


def downgrade() -> None:
    op.drop_column("host", "script_packages_mode")
