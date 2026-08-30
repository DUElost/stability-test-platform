"""Merge heads: f0e1d2c3b4a5 (project_model M1) + h9i0j1k2l3m4 (test_case_result).

两个并行 migration 同挂 e3f4a5b6c7d8——无共同数据依赖，纯合并。

Revision ID: m3n4o5p6q7r8
Revises: f0e1d2c3b4a5, h9i0j1k2l3m4
Create Date: 2026-08-31
"""

from alembic import op

revision = "m3n4o5p6q7r8"
down_revision = ("f0e1d2c3b4a5", "h9i0j1k2l3m4")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
