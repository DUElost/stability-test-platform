"""ADR-0029 P1 — project_device_rule 表 + device.project_pinned。

规则层从 test_project.match_models（JSONB 数组，可重叠、无告警）升格为真表：
model → project 在活跃规则内是函数，DB 层保证（部分唯一索引）；project_pinned
是人工钉住逃生阀（规则不覆盖）。

Revision ID: c4d5e6f7g8h9
Revises: (w1x2y3z4a5b6, r0s9t8u7v6w5, l2m3n4o5p6q7)
Create Date: 2026-08-30
"""

from alembic import op
import sqlalchemy as sa

revision = "c4d5e6f7g8h9"
down_revision = ("w1x2y3z4a5b6", "r0s9t8u7v6w5", "l2m3n4o5p6q7")
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "project_device_rule",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("test_project.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("match_type", sa.String(16), nullable=False,
                  server_default="MODEL"),
        sa.Column("match_value", sa.String(128), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False,
                  server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "match_type IN ('MODEL', 'SERIAL')",
            name="ck_project_device_rule_type",
        ),
    )
    # model → project 在活跃规则内是函数：#2「MLD_LX2 同时属于两个项目」建不出来
    op.create_index(
        "uq_rule_active",
        "project_device_rule",
        ["match_type", sa.text("lower(match_value)")],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )
    op.add_column(
        "device",
        sa.Column(
            "project_pinned",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    # 存量 match_models（JSONB）灌入规则表——冲突（同型号跨项目重叠）跳过，
    # 由活跃唯一索引兜底；此列 P1-B 阶段 drop。
    op.execute(
        sa.text(
            """
            INSERT INTO project_device_rule (project_id, match_type, match_value)
            SELECT tp.id, 'MODEL', value
            FROM test_project tp
            CROSS JOIN LATERAL jsonb_array_elements_text(tp.match_models) AS value
            WHERE tp.source = 'USER'
              AND tp.match_models IS NOT NULL
              AND jsonb_array_length(tp.match_models) > 0
            ON CONFLICT (match_type, lower(match_value)) WHERE is_active DO NOTHING
            """
        )
    )


def downgrade() -> None:
    op.drop_index("uq_rule_active", table_name="project_device_rule")
    op.drop_table("project_device_rule")
    op.drop_column("device", "project_pinned")
