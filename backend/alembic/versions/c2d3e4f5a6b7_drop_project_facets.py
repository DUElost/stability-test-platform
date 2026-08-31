"""ADR-0029 v2.5 D12 — drop test_project.product_line / form_factor。

判据「一个 facet 值得存在当且仅当它能改变某人的行为」：生产 5 行里
2 行 product_line 是 customer 副本、1 行字符串 'None'；form_factor 除
一个 TABLET 全 PHONE。customer 保留（唯一有稳定值）；platforms 派生。

Revision ID: c2d3e4f5a6b7
Revises: l5m6n7o8p9q0
Create Date: 2026-08-31
"""

from alembic import op
import sqlalchemy as sa

revision = "c2d3e4f5a6b7"
down_revision = "b1c2d3e4f5a6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("test_project", "product_line")
    op.drop_column("test_project", "form_factor")


def downgrade() -> None:
    op.add_column("test_project", sa.Column("product_line", sa.String(64), nullable=True))
    op.add_column("test_project", sa.Column("form_factor", sa.String(32), nullable=True))
