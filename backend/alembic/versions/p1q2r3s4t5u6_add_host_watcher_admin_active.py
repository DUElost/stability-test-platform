"""add watcher_admin_active to host

Revision ID: p1q2r3s4t5u6
Revises: f1a2b3c4d5e6
Create Date: 2026-06-07
"""

from alembic import op
import sqlalchemy as sa


revision = "p1q2r3s4t5u6"
down_revision = "f1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 先带 server_default 添加列（PostgreSQL 存量行一次性填充 True），
    # 再移除 server_default，使 ORM 的 Python-side default=True 成为唯一真值源。
    # NOTE: op.alter_column 在 SQLite 下不受支持（SQLite 不允许 ALTER COLUMN）。
    # 测试环境若使用 ALLOW_SQLITE_TESTS=1 应通过 create_all() 建表，而非执行此 migration。
    op.add_column(
        "host",
        sa.Column(
            "watcher_admin_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
    op.alter_column("host", "watcher_admin_active", server_default=None)


def downgrade() -> None:
    op.drop_column("host", "watcher_admin_active")
