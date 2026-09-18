"""drop failure_threshold columns — ADR-0048 移除 run 级通过率判定 (#2734)

Revision ID: f6a7b8c9d0e1
Revises: a1b2c3d4e5f7
Create Date: 2026-09-18

稳定性平台对设备测试通过率无验收要求（设备死机/掉线是施压测试的正常现象），
`failure_threshold` 判定轴造成周期链随机断（run 428 以 5.2% 噪声失败率越 5%
线判 FAILED、链中段终止）。ADR-0048 将 run 终态收窄为「完成即绿、abort 才红」，
阈值列失去全部读者。PG enum 中 PARTIAL_SUCCESS 保留（存量行兼容，不再产出）。
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f6a7b8c9d0e1'
down_revision = 'a1b2c3d4e5f7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_plan_run_failure_threshold", "plan_run", type_="check")
    op.drop_column("plan_run", "failure_threshold")
    op.drop_constraint("ck_plan_failure_threshold", "plan", type_="check")
    op.drop_column("plan", "failure_threshold")


def downgrade() -> None:
    op.add_column(
        "plan",
        sa.Column("failure_threshold", sa.Float(), nullable=False,
                  server_default=sa.text("0.05")),
    )
    op.create_check_constraint(
        "ck_plan_failure_threshold", "plan",
        "failure_threshold >= 0.0 AND failure_threshold <= 1.0",
    )
    op.add_column(
        "plan_run",
        sa.Column("failure_threshold", sa.Float(), nullable=False,
                  server_default=sa.text("0.05")),
    )
    op.create_check_constraint(
        "ck_plan_run_failure_threshold", "plan_run",
        "failure_threshold >= 0.0 AND failure_threshold <= 1.0",
    )
