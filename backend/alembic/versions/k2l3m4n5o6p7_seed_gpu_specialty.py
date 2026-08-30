"""seed gpu specialty — 专项字典新增「GPU压测」（issue #462 P0c）

专项字典是静态种子数据（list_specialties 口径：无写端点，变更走迁移）。

Revision ID: k2l3m4n5o6p7
Revises: j1k2l3m4n5o6
Create Date: 2026-08-31
"""

from alembic import op

revision = "k2l3m4n5o6p7"
down_revision = "j1k2l3m4n5o6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "INSERT INTO specialty (key, display_name, sort_order) "
        "VALUES ('gpu', 'GPU压测', 5) ON CONFLICT (key) DO NOTHING"
    )


def downgrade() -> None:
    # 种子数据不随降级删除（同 e6f7g8h9i0j1/j1k2l3m4n5o6 先例）。
    pass
