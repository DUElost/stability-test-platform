"""B8 G1 — 设备日志事实底座（ADR-0056 D2，ADR-0062）。

新增八张单数事实表：issue_signature / issue_occurrence /
issue_occurrence_event / partition_mode / scan_round /
representative_selection / delivery_version / spill_ledger。

同时给 device_log_event 加可空 occurrence_key 与索引（不回填历史行）。
纯 additive：不改变任何现行读写路径；downgrade 完整撤销。

Revision ID: b8g1c4a7d2e9
Revises:    u9b6d3793a01
Create Date: 2026-11-02
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "b8g1c4a7d2e9"
down_revision = "u9b6d3793a01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "issue_signature",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("signature_raw", sa.Text(), nullable=False),
        sa.Column("normalized_key", sa.String(length=512), nullable=False),
        sa.Column("platform", sa.String(length=16), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column(
            "issue_keys",
            postgresql.JSONB(),
            nullable=False,
        ),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "platform",
            "event_type",
            "normalized_key",
            name="uq_issue_signature_platform_event_normalized",
        ),
    )
    op.create_index(
        "idx_issue_signature_normalized_key",
        "issue_signature",
        ["normalized_key"],
    )

    op.create_table(
        "issue_occurrence",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "signature_id",
            sa.Integer(),
            sa.ForeignKey("issue_signature.id"),
            nullable=False,
        ),
        sa.Column("occurrence_key", sa.String(length=64), nullable=True),
        sa.Column("serial", sa.String(length=128), nullable=False),
        sa.Column("build_version", sa.String(length=256), nullable=True),
        sa.Column("project_key", sa.String(length=64), nullable=True),
        sa.Column("specialty_key", sa.String(length=64), nullable=True),
        sa.Column("risk_level", sa.String(length=8), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("first_seen_source", sa.String(length=16), nullable=False),
        sa.Column("first_seen_plan_run_id", sa.Integer(), nullable=True),
        sa.Column("first_seen_round_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("occurrence_key", name="uq_issue_occurrence_key"),
    )
    op.create_index(
        "idx_issue_occurrence_signature", "issue_occurrence", ["signature_id"]
    )
    op.create_index("idx_issue_occurrence_serial", "issue_occurrence", ["serial"])
    op.create_index(
        "idx_issue_occurrence_plan_run",
        "issue_occurrence",
        ["first_seen_plan_run_id"],
    )

    op.create_table(
        "issue_occurrence_event",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "occurrence_id",
            sa.Integer(),
            sa.ForeignKey("issue_occurrence.id"),
            nullable=False,
        ),
        sa.Column("dle_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "signature_id",
            sa.Integer(),
            sa.ForeignKey("issue_signature.id"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "occurrence_id",
            "dle_id",
            "signature_id",
            name="uq_issue_occurrence_event_mapping",
        ),
    )
    op.create_index(
        "idx_issue_occurrence_event_dle", "issue_occurrence_event", ["dle_id"]
    )

    op.create_table(
        "partition_mode",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("plan_run_id", sa.Integer(), nullable=False),
        sa.Column("platform", sa.String(length=16), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("written_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "plan_run_id", "platform", name="uq_partition_mode_run_platform"
        ),
    )

    op.create_table(
        "scan_round",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("plan_run_id", sa.Integer(), nullable=False),
        sa.Column("platform", sa.String(length=16), nullable=False),
        sa.Column("round_no", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("expected_set", postgresql.JSONB(), nullable=False),
        sa.Column("input_cutoff_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "plan_run_id",
            "platform",
            "round_no",
            name="uq_scan_round_run_platform_round",
        ),
    )
    op.create_index(
        "idx_scan_round_run_platform_status",
        "scan_round",
        ["plan_run_id", "platform", "status"],
    )

    op.create_table(
        "representative_selection",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("plan_run_id", sa.Integer(), nullable=False),
        sa.Column("platform", sa.String(length=16), nullable=False),
        sa.Column("signature_id", sa.Integer(), nullable=False),
        sa.Column("dle_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("selection_status", sa.String(length=16), nullable=False),
        sa.Column("evidence_status", sa.String(length=16), nullable=True),
        sa.Column("selected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "plan_run_id",
            "platform",
            "signature_id",
            name="uq_representative_selection_run_platform_signature",
        ),
    )
    op.create_index(
        "idx_representative_selection_signature",
        "representative_selection",
        ["signature_id"],
    )

    op.create_table(
        "delivery_version",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("plan_run_id", sa.Integer(), nullable=False),
        sa.Column("platform", sa.String(length=16), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("software_versions", postgresql.JSONB(), nullable=False),
        sa.Column("round_ids", postgresql.JSONB(), nullable=False),
        sa.Column("effective_coverage", postgresql.JSONB(), nullable=False),
        sa.Column("issue_ids", postgresql.JSONB(), nullable=False),
        sa.Column("event_ids", postgresql.JSONB(), nullable=False),
        sa.Column("stats_snapshot_version", sa.String(length=64), nullable=True),
        sa.Column("pending_items", postgresql.JSONB(), nullable=False),
        sa.Column("gaps", postgresql.JSONB(), nullable=False),
        sa.Column("primary_folders", postgresql.JSONB(), nullable=False),
        sa.Column("registered_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "plan_run_id",
            "platform",
            "version_no",
            name="uq_delivery_version_run_platform_version",
        ),
    )

    op.create_table(
        "spill_ledger",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("plan_run_id", sa.Integer(), nullable=False),
        sa.Column("platform", sa.String(length=16), nullable=False),
        sa.Column("dle_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("spilled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("dle_id", name="uq_spill_ledger_dle_id"),
    )
    op.create_index(
        "idx_spill_ledger_run_platform", "spill_ledger", ["plan_run_id", "platform"]
    )

    op.add_column(
        "device_log_event",
        sa.Column("occurrence_key", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "idx_device_log_event_occurrence_key",
        "device_log_event",
        ["occurrence_key"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_device_log_event_occurrence_key", table_name="device_log_event"
    )
    op.drop_column("device_log_event", "occurrence_key")

    op.drop_index("idx_spill_ledger_run_platform", table_name="spill_ledger")
    op.drop_table("spill_ledger")

    op.drop_table("delivery_version")

    op.drop_index(
        "idx_representative_selection_signature",
        table_name="representative_selection",
    )
    op.drop_table("representative_selection")

    op.drop_index(
        "idx_scan_round_run_platform_status", table_name="scan_round"
    )
    op.drop_table("scan_round")

    op.drop_table("partition_mode")

    op.drop_index(
        "idx_issue_occurrence_event_dle", table_name="issue_occurrence_event"
    )
    op.drop_table("issue_occurrence_event")

    op.drop_index("idx_issue_occurrence_plan_run", table_name="issue_occurrence")
    op.drop_index("idx_issue_occurrence_serial", table_name="issue_occurrence")
    op.drop_index("idx_issue_occurrence_signature", table_name="issue_occurrence")
    op.drop_table("issue_occurrence")

    op.drop_index(
        "idx_issue_signature_normalized_key", table_name="issue_signature"
    )
    op.drop_table("issue_signature")
