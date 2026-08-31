"""seed specialty 补齐 mtbf/monkey/power-cycle（空库 CI 与生产对齐，issue #462 P0b）

专项字典是静态种子数据（list_specialties 口径：无写端点，变更走迁移）。
e6f7g8h9i0j1 只种了 ops；mtbf/monkey/power-cycle 在生产是手工种子，空库 CI
缺这三行——补齐使空库与生产一致。sleep 已由 i0j1k2l3m4n5 种入。

Revision ID: j1k2l3m4n5o6
Revises: i0j1k2l3m4n5
Create Date: 2026-08-31
"""

from alembic import op

revision = "j1k2l3m4n5o6"
down_revision = "i0j1k2l3m4n5"
branch_labels = None
depends_on = None

_SEEDS = (
    ("mtbf", "MTBF", 1),
    ("power-cycle", "开关机", 2),
    ("monkey", "MONKEY", 3),
)


def upgrade() -> None:
    for key, display_name, sort_order in _SEEDS:
        op.execute(
            "INSERT INTO specialty (key, display_name, sort_order) "
            f"VALUES ('{key}', '{display_name}', {sort_order}) ON CONFLICT (key) DO NOTHING"
        )


def downgrade() -> None:
    # 种子数据不随降级删除（同 e6f7g8h9i0j1 对 ops 的先例）：既有 plan.specialty_id
    # 外键引用在，删行会破坏运行中的 PlanRun 追溯。
    pass
