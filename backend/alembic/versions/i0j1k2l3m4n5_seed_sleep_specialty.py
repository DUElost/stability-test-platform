"""seed sleep specialty — 专项字典新增「休眠唤醒」（issue #462 P0a）

专项字典是静态种子数据（list_specialties 口径：无写端点，变更走迁移）。
power-cycle / mtbf / monkey 已存在，此处只补 sleep。

Revision ID: i0j1k2l3m4n5
Revises: h9i0j1k2l3m4
Create Date: 2026-08-31
"""

from alembic import op

revision = "i0j1k2l3m4n5"
down_revision = "h9i0j1k2l3m4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "INSERT INTO specialty (key, display_name, sort_order) "
        "VALUES ('sleep', '休眠唤醒', 4) ON CONFLICT (key) DO NOTHING"
    )


def downgrade() -> None:
    op.execute("DELETE FROM specialty WHERE key = 'sleep'")
