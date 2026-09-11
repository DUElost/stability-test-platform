# -*- coding: utf-8 -*-
"""通知投递事实层 notification_delivery（#1167 P4 / ADR-0036 D6）

- 每行 = 一次「通知 × 通道」投递：state（生命周期词表）+ outcome（尝试结果类）
  + attempt_count + last_error + 时间戳；
- unique(notification_log_id, channel_id) 保证 1:N 里的通道唯一，
  重试在原行上累加 attempt_count；
- 与 NotificationLog.context.channel_delivery（JSONB 过渡记录）双写，
  本表为权威（历史日志无本表行时回落 JSONB 读取）。

Revision ID: cc33dd44ee55
Revises: bb22cc33dd44
Create Date: 2026-09-11
"""

from alembic import op
import sqlalchemy as sa

revision = "cc33dd44ee55"
down_revision = "bb22cc33dd44"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "notification_delivery",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "notification_log_id",
            sa.Integer(),
            sa.ForeignKey("notification_logs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "channel_id",
            sa.Integer(),
            sa.ForeignKey("notification_channels.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("channel_type", sa.String(32), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="requested"),
        sa.Column("outcome", sa.String(32), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "notification_log_id", "channel_id",
            name="uq_notification_delivery_log_channel",
        ),
    )
    op.create_index(
        "ix_notification_delivery_log", "notification_delivery",
        ["notification_log_id"],
    )
    op.create_index(
        "ix_notification_delivery_state", "notification_delivery", ["state"],
    )
    op.create_index(
        "ix_notification_delivery_outcome", "notification_delivery", ["outcome"],
    )


def downgrade() -> None:
    op.drop_index("ix_notification_delivery_outcome", table_name="notification_delivery")
    op.drop_index("ix_notification_delivery_state", table_name="notification_delivery")
    op.drop_index("ix_notification_delivery_log", table_name="notification_delivery")
    op.drop_table("notification_delivery")
