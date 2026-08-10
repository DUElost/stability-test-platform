"""Add device_log_event table + job_log_signal / plan_run_artifact extensions (ADR-0028 D1/D7).

Revision ID: p3q4r5s6t7u8
Revises: o2p3q4r5s6t7
Create Date: 2026-08-09

- device_log_event: authoritative device log event lifecycle
- job_log_signal.device_log_event_id FK (SET NULL on delete)
- job_log_signal.job_id: CASCADE → SET NULL, nullable
- plan_run_artifact.scan_round_id: merge round boundary (D7)
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision = "p3q4r5s6t7u8"
down_revision = "o2p3q4r5s6t7"
branch_labels = None
depends_on = None


def _drop_job_log_signal_job_fk(bind) -> None:
    inspector = inspect(bind)
    for fk in inspector.get_foreign_keys("job_log_signal"):
        if fk.get("constrained_columns") == ["job_id"]:
            op.drop_constraint(fk["name"], "job_log_signal", type_="foreignkey")
            return


def upgrade() -> None:
    bind = op.get_bind()

    op.create_table(
        "device_log_event",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("serial", sa.String(128), nullable=False),
        sa.Column("platform", sa.String(16), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("event_subtype", sa.String(128), nullable=True),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("device_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("state", sa.String(32), nullable=False, server_default="DETECTED"),
        sa.Column("local_path", sa.String(1024), nullable=False),
        sa.Column("remote_path", sa.String(1024), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("checksum", sa.String(64), nullable=True),
        sa.Column("plan_run_id", sa.Integer(), sa.ForeignKey("plan_run.id", ondelete="SET NULL"), nullable=True),
        sa.Column("host_id", sa.String(64), sa.ForeignKey("host.id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("job_instance.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_device_log_event_plan_state", "device_log_event", ["plan_run_id", "state"])
    op.create_index(
        "idx_device_log_event_host_state_detected",
        "device_log_event",
        ["host_id", "state", "detected_at"],
    )
    op.create_index("idx_device_log_event_serial_detected", "device_log_event", ["serial", "detected_at"])
    op.create_index("idx_device_log_event_state_updated", "device_log_event", ["state", "updated_at"])

    columns = {c["name"] for c in inspect(bind).get_columns("job_log_signal")}
    if "device_log_event_id" not in columns:
        op.add_column(
            "job_log_signal",
            sa.Column(
                "device_log_event_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("device_log_event.id", ondelete="SET NULL"),
                nullable=True,
            ),
        )

    _drop_job_log_signal_job_fk(bind)
    op.alter_column("job_log_signal", "job_id", existing_type=sa.Integer(), nullable=True)
    op.create_foreign_key(
        "fk_job_log_signal_job_id",
        "job_log_signal",
        "job_instance",
        ["job_id"],
        ["id"],
        ondelete="SET NULL",
    )

    pra_columns = {c["name"] for c in inspect(bind).get_columns("plan_run_artifact")}
    if "scan_round_id" not in pra_columns:
        op.add_column("plan_run_artifact", sa.Column("scan_round_id", sa.String(64), nullable=True))
        op.create_index(
            "idx_plan_run_artifact_run_round",
            "plan_run_artifact",
            ["plan_run_id", "scan_round_id"],
        )


def downgrade() -> None:
    bind = op.get_bind()

    pra_columns = {c["name"] for c in inspect(bind).get_columns("plan_run_artifact")}
    if "scan_round_id" in pra_columns:
        op.drop_index("idx_plan_run_artifact_run_round", table_name="plan_run_artifact")
        op.drop_column("plan_run_artifact", "scan_round_id")

    jls_columns = {c["name"] for c in inspect(bind).get_columns("job_log_signal")}
    if "device_log_event_id" in jls_columns:
        op.drop_column("job_log_signal", "device_log_event_id")

    op.drop_constraint("fk_job_log_signal_job_id", "job_log_signal", type_="foreignkey")
    op.alter_column("job_log_signal", "job_id", existing_type=sa.Integer(), nullable=False)
    op.create_foreign_key(
        "job_log_signal_job_id_fkey",
        "job_log_signal",
        "job_instance",
        ["job_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.drop_index("idx_device_log_event_state_updated", table_name="device_log_event")
    op.drop_index("idx_device_log_event_serial_detected", table_name="device_log_event")
    op.drop_index("idx_device_log_event_host_state_detected", table_name="device_log_event")
    op.drop_index("idx_device_log_event_plan_state", table_name="device_log_event")
    op.drop_table("device_log_event")
