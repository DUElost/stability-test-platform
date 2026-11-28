"""B13 U1 — Unified network supply schema (ADR-0066 / #4212).

- New singular table ``device_network_session`` with ADR-0066 D2.2 CHECK
  constraints and D2.3 partial unique active-per-device index.
- Explicit columns on ``host``: ``usbnet_max_devices`` (Integer, nullable) and
  ``usbnet_min_allowed_iat_by_egress`` (JSONB, nullable).

Pure additive; ``downgrade()`` fully reverses. Head prior: ``b12u1a2b3c4d5``.

Revision ID: b13u1c2d3e4f5
Revises:    b12u1a2b3c4d5
Create Date: 2026-11-28
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "b13u1c2d3e4f5"
down_revision = "b12u1a2b3c4d5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "host",
        sa.Column("usbnet_max_devices", sa.Integer(), nullable=True),
    )
    op.add_column(
        "host",
        sa.Column(
            "usbnet_min_allowed_iat_by_egress",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )

    op.create_table(
        "device_network_session",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "device_id",
            sa.Integer(),
            sa.ForeignKey("device.id"),
            nullable=False,
        ),
        sa.Column(
            "host_id",
            sa.String(length=64),
            sa.ForeignKey("host.id", ondelete="CASCADE", onupdate="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "resource_pool_id",
            sa.Integer(),
            sa.ForeignKey("resource_pool.id"),
            nullable=False,
        ),
        sa.Column("resource_type", sa.String(length=32), nullable=False),
        sa.Column(
            "egress",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'direct'"),
        ),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column(
            "plan_run_id",
            sa.Integer(),
            sa.ForeignKey("plan_run.id"),
            nullable=True,
        ),
        sa.Column(
            "job_instance_id",
            sa.Integer(),
            sa.ForeignKey("job_instance.id"),
            nullable=True,
        ),
        sa.Column(
            "lease_id",
            sa.Integer(),
            sa.ForeignKey("device_leases.id"),
            nullable=True,
        ),
        sa.Column("generation", sa.Integer(), nullable=True),
        sa.Column(
            "legacy_path",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("release_reason", sa.String(length=64), nullable=True),
        sa.Column("assigned_ip", sa.String(length=64), nullable=True),
        sa.Column("usb_devpath", sa.String(length=128), nullable=True),
        sa.Column(
            "prior_network_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("created_by", sa.String(length=128), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(lease_id IS NOT NULL AND generation IS NOT NULL) OR ("
            "lease_id IS NULL AND generation IS NULL AND source = 'job' AND ("
            "status = 'RESERVED' OR "
            "(status = 'RELEASED' AND release_reason = 'unclaimed_canceled')"
            "))",
            name="ck_device_network_session_lease_binding",
        ),
        sa.CheckConstraint(
            "(source = 'job' AND job_instance_id IS NOT NULL) OR "
            "(source = 'manual' AND job_instance_id IS NULL AND expires_at IS NOT NULL)",
            name="ck_device_network_session_source_fields",
        ),
        sa.CheckConstraint(
            "status IN ('RESERVED', 'PROVISIONING', 'ACTIVE', 'RELEASING', 'RELEASE_FAILED', 'RELEASED')",
            name="ck_device_network_session_status",
        ),
        sa.CheckConstraint(
            "resource_type IN ('wifi', 'usb_net')",
            name="ck_device_network_session_resource_type",
        ),
        sa.CheckConstraint(
            "egress IN ('direct', 'vpn')",
            name="ck_device_network_session_egress",
        ),
    )

    op.create_index(
        "uq_device_network_session_active_per_device",
        "device_network_session",
        ["device_id"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('RESERVED', 'PROVISIONING', 'ACTIVE', 'RELEASING', 'RELEASE_FAILED')"
        ),
    )
    op.create_index(
        "ix_device_network_session_host_status",
        "device_network_session",
        ["host_id", "status"],
    )
    op.create_index(
        "ix_device_network_session_pool_status",
        "device_network_session",
        ["resource_pool_id", "status"],
    )
    op.create_index(
        "ix_device_network_session_job",
        "device_network_session",
        ["job_instance_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_device_network_session_job", table_name="device_network_session")
    op.drop_index("ix_device_network_session_pool_status", table_name="device_network_session")
    op.drop_index("ix_device_network_session_host_status", table_name="device_network_session")
    op.drop_index(
        "uq_device_network_session_active_per_device",
        table_name="device_network_session",
    )
    op.drop_table("device_network_session")
    op.drop_column("host", "usbnet_min_allowed_iat_by_egress")
    op.drop_column("host", "usbnet_max_devices")
