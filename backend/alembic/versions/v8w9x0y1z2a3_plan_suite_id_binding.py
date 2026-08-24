"""ADR-0030 v1.4 P1b-B — plan.suite_id 可空外键（套件绑定）。

NULL = P0 文件真源模式（不加门禁，存量行为完全不变）；
非空 = 托管模式（precheck 五步门禁 + prepare 冻结 dispatch_suite，随 PR-C 落地）。
本迁移只建列与索引，无回填、无读路径——纯 additive（ADR-0008）。

Revision ID: v8w9x0y1z2a3
Revises:    u7v8w9x0y1z2
Create Date: 2026-08-24
"""

from alembic import op
import sqlalchemy as sa

revision = "v8w9x0y1z2a3"
down_revision = "u7v8w9x0y1z2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "plan",
        sa.Column("suite_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_plan_suite_id_test_suite",
        "plan",
        "test_suite",
        ["suite_id"],
        ["id"],
    )
    op.create_index("idx_plan_suite", "plan", ["suite_id"])


def downgrade() -> None:
    op.drop_index("idx_plan_suite", table_name="plan")
    op.drop_constraint("fk_plan_suite_id_test_suite", "plan", type_="foreignkey")
    op.drop_column("plan", "suite_id")
