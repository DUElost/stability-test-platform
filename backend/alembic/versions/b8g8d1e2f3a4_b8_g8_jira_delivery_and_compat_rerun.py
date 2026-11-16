"""B8 G8 — 交付版本提单绑定、发生归因关联与受控兼容补跑（ADR-0062 D10/D15）。

1. 新增单数表 ``compat_rerun``：Owner 显式发起的单个 DEDUP_FIRST 分区兼容补跑记录；
2. 扩展 ``jira_run``：绑定平台、交付版本、兼容补跑、独立工作区与归因缺口；
3. 新增单数表 ``jira_occurrence_link``：有证据支持的 issue_key ↔ 问题/发生关联。

纯 additive：保留 upload / plan_run 兼容链路；downgrade 完整撤销。

Revision ID: b8g8d1e2f3a4
Revises:    b8g6d0e1f234
Create Date: 2026-11-16
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "b8g8d1e2f3a4"
down_revision = "b8g6d0e1f234"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "compat_rerun",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("plan_run_id", sa.Integer(), nullable=False),
        sa.Column("platform", sa.String(length=16), nullable=False),
        sa.Column("request_key", sa.String(length=128), nullable=False),
        sa.Column("requested_by", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="PENDING"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "source_delivery_version_id",
            sa.Integer(),
            sa.ForeignKey("delivery_version.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("source_delivery_version_no", sa.Integer(), nullable=True),
        sa.Column(
            "coverage_scope",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "source_records",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "missing_items",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "is_partial",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("artifact_dir", sa.String(length=1024), nullable=True),
        sa.Column("merge_xls_path", sa.String(length=1024), nullable=True),
        sa.Column("manifest_path", sa.String(length=1024), nullable=True),
        sa.Column("jira_run_id", sa.Integer(), nullable=True),
        sa.Column("error", sa.String(length=1024), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "plan_run_id",
            "platform",
            "request_key",
            name="uq_compat_rerun_run_platform_request",
        ),
    )
    op.create_index(
        "idx_compat_rerun_run_platform",
        "compat_rerun",
        ["plan_run_id", "platform"],
    )

    op.add_column("jira_run", sa.Column("platform", sa.String(length=16), nullable=True))
    op.add_column(
        "jira_run",
        sa.Column(
            "delivery_version_id",
            sa.Integer(),
            sa.ForeignKey("delivery_version.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "jira_run", sa.Column("delivery_version_no", sa.Integer(), nullable=True),
    )
    op.add_column(
        "jira_run",
        sa.Column(
            "compat_rerun_id",
            sa.Integer(),
            sa.ForeignKey("compat_rerun.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "jira_run", sa.Column("workspace_dir", sa.String(length=1024), nullable=True),
    )
    op.add_column(
        "jira_run", sa.Column("attribution_status", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "jira_run",
        sa.Column(
            "attribution_gaps",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.create_index(
        "idx_jira_run_delivery_version", "jira_run", ["delivery_version_id"],
    )
    op.create_index(
        "idx_jira_run_compat_rerun", "jira_run", ["compat_rerun_id"],
    )

    op.create_table(
        "jira_occurrence_link",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "jira_run_id",
            sa.Integer(),
            sa.ForeignKey("jira_run.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("plan_run_id", sa.Integer(), nullable=False),
        sa.Column("platform", sa.String(length=16), nullable=False),
        sa.Column(
            "delivery_version_id",
            sa.Integer(),
            sa.ForeignKey("delivery_version.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("delivery_version_no", sa.Integer(), nullable=True),
        sa.Column(
            "signature_id",
            sa.Integer(),
            sa.ForeignKey("issue_signature.id"),
            nullable=False,
        ),
        sa.Column(
            "occurrence_id",
            sa.Integer(),
            sa.ForeignKey("issue_occurrence.id"),
            nullable=False,
        ),
        sa.Column("issue_key", sa.String(length=64), nullable=False),
        sa.Column("evidence_source", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "jira_run_id",
            "occurrence_id",
            "issue_key",
            name="uq_jira_occurrence_link_run_occ_key",
        ),
    )
    op.create_index(
        "idx_jira_occurrence_link_run", "jira_occurrence_link", ["jira_run_id"],
    )
    op.create_index(
        "idx_jira_occurrence_link_occ", "jira_occurrence_link", ["occurrence_id"],
    )
    op.create_index(
        "idx_jira_occurrence_link_sig", "jira_occurrence_link", ["signature_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_jira_occurrence_link_sig", table_name="jira_occurrence_link")
    op.drop_index("idx_jira_occurrence_link_occ", table_name="jira_occurrence_link")
    op.drop_index("idx_jira_occurrence_link_run", table_name="jira_occurrence_link")
    op.drop_table("jira_occurrence_link")

    op.drop_index("idx_jira_run_compat_rerun", table_name="jira_run")
    op.drop_index("idx_jira_run_delivery_version", table_name="jira_run")
    op.drop_column("jira_run", "attribution_gaps")
    op.drop_column("jira_run", "attribution_status")
    op.drop_column("jira_run", "workspace_dir")
    op.drop_column("jira_run", "compat_rerun_id")
    op.drop_column("jira_run", "delivery_version_no")
    op.drop_column("jira_run", "delivery_version_id")
    op.drop_column("jira_run", "platform")

    op.drop_index("idx_compat_rerun_run_platform", table_name="compat_rerun")
    op.drop_table("compat_rerun")
