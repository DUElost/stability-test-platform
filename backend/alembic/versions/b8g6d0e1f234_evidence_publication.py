"""B8 G6 — S8 代表证据发布记录（ADR-0062 D8/D9）。

新增单数表 evidence_publication：以 (实际 PlanRun, 平台, 代表 DLE) 为唯一身份，
记录来源类型与源定位、固定目的相对路径与命名规则版本、重试状态、已校验哈希
与字节数。目的相对路径对非 FAILED 行唯一（部分唯一索引）：不同身份的落点
冲突拒绝覆盖，冲突行以 FAILED 落库保留诊断路径。

纯 additive：不改变现行上传/提取路径；downgrade 完整撤销。

Revision ID: b8g6d0e1f234
Revises:    b8g5c0a1b2c3
Create Date: 2026-11-13
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "b8g6d0e1f234"
down_revision = "b8g5c0a1b2c3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "evidence_publication",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("plan_run_id", sa.Integer(), nullable=False),
        sa.Column("platform", sa.String(length=16), nullable=False),
        sa.Column("dle_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("root_plan_run_id", sa.Integer(), nullable=False),
        sa.Column("source_kind", sa.String(length=8), nullable=False),
        sa.Column("source_path", sa.String(length=1024), nullable=True),
        sa.Column("collection_folder", sa.String(length=255), nullable=True),
        sa.Column("naming_rule_version", sa.String(length=64), nullable=True),
        sa.Column("destination_rel_path", sa.String(length=1024), nullable=True),
        sa.Column(
            "state", sa.String(length=16), nullable=False, server_default="PENDING",
        ),
        sa.Column(
            "attempts", sa.Integer(), nullable=False, server_default="0",
        ),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("copy_claim_token", sa.String(length=64), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("checksum", sa.String(length=64), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("failure_reason", sa.String(length=64), nullable=True),
        sa.Column("executor_host_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "plan_run_id", "platform", "dle_id",
            name="uq_evidence_publication_run_platform_dle",
        ),
    )
    op.create_index(
        "uq_evidence_publication_destination",
        "evidence_publication",
        ["destination_rel_path"],
        unique=True,
        postgresql_where=sa.text("state <> 'FAILED'"),
    )
    op.create_index(
        "idx_evidence_publication_host_state",
        "evidence_publication",
        ["executor_host_id", "state"],
    )
    op.create_index(
        "idx_evidence_publication_run_state",
        "evidence_publication",
        ["plan_run_id", "platform", "state"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_evidence_publication_run_state", table_name="evidence_publication",
    )
    op.drop_index(
        "idx_evidence_publication_host_state", table_name="evidence_publication",
    )
    op.drop_index(
        "uq_evidence_publication_destination", table_name="evidence_publication",
    )
    op.drop_table("evidence_publication")
