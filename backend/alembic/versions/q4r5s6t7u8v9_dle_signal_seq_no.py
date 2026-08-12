"""Persist DeviceLogEvent.signal_seq_no for late job_log_signal linking (#214).

Revision ID: q4r5s6t7u8v9
Revises: p3q4r5s6t7u8
Create Date: 2026-08-12

Agent emits log_signal to outbox then immediately POSTs DeviceLogEvent.
Control-plane job_log_signal rows arrive later via /log-signals, so the
forward UPDATE on DLE ingest hits 0 rows. Storing signal_seq_no lets
/log-signals reverse-link when the signal finally lands.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "q4r5s6t7u8v9"
down_revision = "p3q4r5s6t7u8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {c["name"] for c in inspect(bind).get_columns("device_log_event")}
    if "signal_seq_no" not in columns:
        op.add_column(
            "device_log_event",
            sa.Column("signal_seq_no", sa.BigInteger(), nullable=True),
        )
        op.create_index(
            "idx_device_log_event_job_signal_seq",
            "device_log_event",
            ["job_id", "signal_seq_no"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    indexes = {i["name"] for i in inspect(bind).get_indexes("device_log_event")}
    if "idx_device_log_event_job_signal_seq" in indexes:
        op.drop_index("idx_device_log_event_job_signal_seq", table_name="device_log_event")
    columns = {c["name"] for c in inspect(bind).get_columns("device_log_event")}
    if "signal_seq_no" in columns:
        op.drop_column("device_log_event", "signal_seq_no")
