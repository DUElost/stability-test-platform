"""ADR-0029 P1-B — drop test_project.platform + form_factor 收敛 enum。

platform 是事实层字段（设备心跳采集），与 customer 不正交（中兴几乎必然
展锐），A57 标 MTK 实际全 UNISOC 的矛盾随删列消失。改为从设备派生
``platforms: distinct(device.platform)``。

form_factor 收敛为 CHECK enum PHONE/TABLET/WATCH/OTHER——生产存在
'手机'（USER 行）与 'PHONE'（SEED 行）两套词表，先映射再上约束。

Revision ID: d5e6f7g8h9i0
Revises: c4d5e6f7g8h9
Create Date: 2026-08-30
"""

from alembic import op
import sqlalchemy as sa

revision = "d5e6f7g8h9i0"
down_revision = "c4d5e6f7g8h9"
branch_labels = None
depends_on = None

_FORM_FACTOR_ENUM = ("PHONE", "TABLET", "WATCH", "OTHER")


def upgrade() -> None:
    op.execute(
        "UPDATE test_project SET form_factor = 'PHONE' WHERE form_factor = '手机'"
    )
    op.execute(
        sa.text(
            """
            UPDATE test_project SET form_factor = 'OTHER'
            WHERE form_factor IS NOT NULL
              AND form_factor NOT IN :values
            """
        ).bindparams(sa.bindparam("values", expanding=True))
    )
    # SQLite 测试环境不建 CHECK（CREATE TABLE 处才有约束；此处仅 PG 生效）
    op.execute(
        sa.text(
            "ALTER TABLE test_project ADD CONSTRAINT ck_test_project_form_factor "
            "CHECK (form_factor IS NULL OR form_factor IN ('PHONE','TABLET','WATCH','OTHER'))"
        )
    )
    op.drop_column("test_project", "platform")


def downgrade() -> None:
    op.add_column(
        "test_project",
        sa.Column("platform", sa.String(64), nullable=True),
    )
    op.drop_constraint("ck_test_project_form_factor", "test_project", type_="check")
