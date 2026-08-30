"""ADR-0029 v2.5 D10 M1 — project_device_rule 收敛为 project_model。

语义纠正：这张表是「项目 = 机型集合」的成员定义（唯一事实源），不是
「规则」。改名 + 索引改名（partial unique 保留——同型号不能双归属）。

Revision ID: a7b8c9d0e1f2
Revises: f6g7h8i9j0k1
Create Date: 2026-08-31
"""

from alembic import op

revision = "f0e1d2c3b4a5"
down_revision = "e3f4a5b6c7d8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.rename_table("project_device_rule", "project_model")
    op.execute("ALTER INDEX uq_rule_active RENAME TO uq_project_model_active")
    op.execute(
        "ALTER TABLE project_model RENAME CONSTRAINT "
        "ck_project_device_rule_type TO ck_project_model_type"
    )


def downgrade() -> None:
    op.execute("ALTER INDEX uq_project_model_active RENAME TO uq_rule_active")
    op.execute(
        "ALTER TABLE project_device_rule RENAME CONSTRAINT "
        "ck_project_model_type TO ck_project_device_rule_type"
    )
    op.rename_table("project_model", "project_device_rule")
