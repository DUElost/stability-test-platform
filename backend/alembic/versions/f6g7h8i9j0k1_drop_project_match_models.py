"""ADR-0029 P1 收尾 — drop test_project.match_models。

规则层已完整迁移到 project_device_rule（P1-A 起写侧停写、读侧派生），
本 migration 纯删列。项目对外 match_models 字段仍由规则表派生返回
（_rule_values_for_project），API 契约不变。

Revision ID: f6g7h8i9j0k1
Revises: e6f7g8h9i0j1
Create Date: 2026-08-30
"""

from alembic import op
import sqlalchemy as sa

revision = "f6g7h8i9j0k1"
down_revision = "e6f7g8h9i0j1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("test_project", "match_models")


def downgrade() -> None:
    op.add_column(
        "test_project",
        sa.Column("match_models", sa.dialects.postgresql.JSONB(),
                  nullable=False, server_default=sa.text("'[]'")),
    )
