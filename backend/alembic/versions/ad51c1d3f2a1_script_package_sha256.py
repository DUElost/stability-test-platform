"""script.package_sha256 —— ADR-0051 D3 / ADR-0033 v1.13 C1 双列（Phase 2a）

Revision ID: ad51c1d3f2a1
Revises: 9f8e7d6c5b4a
Create Date: 2026-09-22

整包 sha（``tool_manifest.json`` 登记条目的 ``package_sha256``），与
``content_sha256``（入口文件 sha）语义分离。可空：迁移不读文件系统、不回填——
回填由下一次 ``POST /scripts/scan`` 按 ``(name, version)`` 从 manifest 完成
（``script_catalog.load_package_index``）。down 仅删本列。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "ad51c1d3f2a1"
down_revision = "9f8e7d6c5b4a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("script", sa.Column("package_sha256", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("script", "package_sha256")
