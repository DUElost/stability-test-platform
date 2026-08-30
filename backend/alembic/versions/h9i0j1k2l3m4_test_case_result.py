"""test_case_result — PlanRun 逐条用例结果（ADR-0030 P2）

Revision ID: h9i0j1k2l3m4
Revises: e3f4a5b6c7d8
Create Date: 2026-08-31
"""

from alembic import op
import sqlalchemy as sa

revision = "h9i0j1k2l3m4"
down_revision = "e3f4a5b6c7d8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "test_case_result",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("plan_run_id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("suite_id", sa.Integer(), nullable=True),
        sa.Column("case_id", sa.Integer(), nullable=True),
        sa.Column("case_name", sa.String(length=512), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("artifact_uri", sa.String(length=1024), nullable=True),
        sa.Column("run_dir", sa.String(length=256), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["test_case.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["job_id"], ["job_instance.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["plan_run_id"], ["plan_run.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["suite_id"], ["test_suite.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "case_name", name="uq_test_case_result_job_case"),
    )
    op.create_index("idx_test_case_result_plan_run", "test_case_result", ["plan_run_id"])
    op.create_index("idx_test_case_result_job", "test_case_result", ["job_id"])


def downgrade() -> None:
    op.drop_index("idx_test_case_result_job", table_name="test_case_result")
    op.drop_index("idx_test_case_result_plan_run", table_name="test_case_result")
    op.drop_table("test_case_result")
