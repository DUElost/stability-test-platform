"""修复 uq_project_model_active 唯一索引（P0 回归）——同型号不能双归属。

背景：c4d5e6f7g8h9 建的是 (match_type, lower(match_value)) WHERE is_active
部分唯一索引；f0e1d2c3b4a5 只 RENAME 索引（列集未变）；a9b8c7d6e5f4 删
match_type 列时 PostgreSQL **静默连带删除**引用该列的索引，且未按新形态
（lower(match_value) 单列）重建——生产库从此只剩主键。「model → project
是全函数」是 v2.5 派生归属的地基，现仅剩应用层一道检查。

本迁移：先查活跃重复（有则 fail 要求人工裁决——不应存在，v2.5 迁移时
抹平过偏差），再按模型声明形态重建部分唯一索引；顺手补 device.model
索引（全部归属读路径的 join 键，生产 1000+ 台时第一个要补的）。

Revision ID: f8a9b0c1d2e3
Revises: d6e7f8a9b0c1
Create Date: 2026-08-31
"""

from alembic import op
import sqlalchemy as sa

revision = "f8a9b0c1d2e3"
down_revision = "d6e7f8a9b0c1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1) 活跃重复检查：唯一索引建立前的硬前提。有重复 = 数据已双归属，
    #    建索引必然失败且错误信息难懂——先显式 fail 要求人工裁决。
    dups = op.get_bind().execute(
        sa.text(
            "SELECT lower(match_value) FROM project_model WHERE is_active "
            "GROUP BY 1 HAVING count(*) > 1"
        )
    ).fetchall()
    if dups:
        raise RuntimeError(
            "project_model 存在活跃重复型号，须先人工裁决："
            + ", ".join(r[0] for r in dups)
        )
    op.execute(
        "CREATE UNIQUE INDEX uq_project_model_active "
        "ON project_model (lower(match_value)) WHERE is_active"
    )
    # 2) device.model：全部归属读路径（_summary_rows / _platforms_map /
    #    inventory / 派发推断 / suite 门禁）的 join 键
    op.execute("CREATE INDEX idx_device_model ON device (model)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_project_model_active")
    op.execute("DROP INDEX IF EXISTS idx_device_model")
