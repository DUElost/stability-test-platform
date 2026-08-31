"""customer 字典表（ADR-0029 D12）——test_project.customer 编辑下拉的数据源。

与 specialty 同范式：静态种子数据、无写端点，变更走迁移。**列不动**——
customer 列保持自由文本字符串，字典表只承担输入建议。seed 从
test_project.customer 现有值去重回填（出现次数多者排前，保证生产 4 个
客户名按频次排序）。

Revision ID: d6e7f8a9b0c1
Revises: c2d3e4f5a6b7
Create Date: 2026-08-31
"""

from alembic import op
import sqlalchemy as sa

revision = "d6e7f8a9b0c1"
down_revision = "c2d3e4f5a6b7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "customer",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=64), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("key", name="uq_customer_key"),
    )
    op.create_index("idx_customer_sort", "customer", ["sort_order"])
    op.execute(
        sa.text(
            """
            INSERT INTO customer (key, display_name, sort_order)
            SELECT customer, customer,
                   ROW_NUMBER() OVER (ORDER BY cnt DESC, customer)
            FROM (
                SELECT customer, count(*) AS cnt
                FROM test_project
                WHERE customer IS NOT NULL AND customer != ''
                GROUP BY customer
            ) t
            ON CONFLICT (key) DO NOTHING
            """
        )
    )


def downgrade() -> None:
    op.drop_index("idx_customer_sort", table_name="customer")
    op.drop_table("customer")
