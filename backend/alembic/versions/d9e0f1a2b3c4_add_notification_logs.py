"""Add notification_logs table for unified notification history.

Revision ID: d9e0f1a2b3c4
Revises: c8d9e0f1a2b3
Create Date: 2026-07-14
"""

from alembic import op
import sqlalchemy as sa


revision = "d9e0f1a2b3c4"
down_revision = "c8d9e0f1a2b3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "notification_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source", sa.Enum("PLATFORM", "ALERTMANAGER", name="notificationsource"), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("severity", sa.Enum("info", "warning", "critical", name="notificationseverity"), nullable=False),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("message", sa.Text(), nullable=False, server_default=""),
        sa.Column("context", sa.JSON(), server_default="{}"),
        sa.Column("read", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_notification_logs_read", "notification_logs", ["read"])
    op.create_index("ix_notification_logs_created_at", "notification_logs", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_notification_logs_created_at", table_name="notification_logs")
    op.drop_index("ix_notification_logs_read", table_name="notification_logs")
    op.drop_table("notification_logs")
    sa.Enum(name="notificationseverity").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="notificationsource").drop(op.get_bind(), checkfirst=True)
