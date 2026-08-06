"""step_trace.exit_code / step_metadata — 步骤超时信息透传（#173）

Agent ``StepResult`` 已区分 wall-clock 超时（exit 124）与停滞超时（exit 125 +
``metadata.timeout_kind``），但控制面/前端无法看到。本迁移给 ``step_trace``
增加两列，Agent 上报、StepTraceOut、plan-run events 全程透传
（API 字段名保持 ``metadata``，DB 列名 ``step_metadata`` 避开 SQLAlchemy 保留名）。

Revision ID: n1o2p3q4r5s6
Revises: m1n2o3p4q5r6
Create Date: 2026-08-06
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "n1o2p3q4r5s6"
down_revision = "m1n2o3p4q5r6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "step_trace",
        sa.Column("exit_code", sa.Integer(), nullable=True),
    )
    op.add_column(
        "step_trace",
        sa.Column("step_metadata", JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("step_trace", "step_metadata")
    op.drop_column("step_trace", "exit_code")
