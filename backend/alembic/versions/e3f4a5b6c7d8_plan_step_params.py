"""plan_step.params — 步骤级参数覆盖（#508）

PlanStep 配 params（JSONB，可空）后，执行时以步骤级参数**覆盖/合并**脚本版本
default_params（step.params 优先）。NULL/{} = 纯 default_params。

不违反「版本即参数」不变量：版本不可变仅约束脚本 default_params，步骤级
params 是 Plan 侧声明、随 Plan 走，可变是刻意的（Web UI 改 apk_path 不必
新建脚本版本）。

Revision ID: e3f4a5b6c7d8
Revises: g5a6b7c8d9e0
Create Date: 2026-08-30
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "e3f4a5b6c7d8"
down_revision = "g5a6b7c8d9e0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "plan_step",
        sa.Column("params", JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("plan_step", "params")
