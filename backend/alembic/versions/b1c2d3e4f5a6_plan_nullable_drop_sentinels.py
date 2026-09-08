"""ADR-0029 v2.5 D11 M4 — plan.project_id 恢复可空 + 删 GENERIC/LEGACY 哨兵。

显式性属于 API/UI 契约（UI 二选一单选），存储层 NULL = 不限——P1-B2
收 NOT NULL 的动机对但放错了层。plan_run.project_id 是历史快照（v2.5
保留），其 FK 引用会阻止删哨兵行——drop FK（快照本就不该引用活表）。

Revision ID: b1c2d3e4f5a6
Revises: l5m6n7o8p9q0
Create Date: 2026-08-31
"""

from alembic import op
from sqlalchemy import text

revision = "b1c2d3e4f5a6"
down_revision = "l5m6n7o8p9q0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. plan 恢复可空
    op.alter_column("plan", "project_id", nullable=True)
    # 2. 存量回填：GENERIC/LEGACY 归属 → NULL（显式「不限」）
    op.execute(
        "UPDATE plan SET project_id = NULL WHERE project_id IN "
        "(SELECT id FROM test_project WHERE project_key IN ('GENERIC', 'LEGACY'))"
    )
    # 3. plan_run 快照 FK drop（历史快照不引用活表，删哨兵行的前提）
    op.execute(
        "ALTER TABLE plan_run DROP CONSTRAINT IF EXISTS plan_run_project_id_fkey"
    )
    # 4. 删哨兵行
    op.execute(
        "DELETE FROM test_project WHERE project_key IN ('GENERIC', 'LEGACY')"
    )


def downgrade() -> None:
    # #935: 哨兵行在 upgrade 中删除且未保留原 id——历史 plan_run.project_id
    # 若引用旧哨兵 id（或任何已删项目），此处 ADD CONSTRAINT 的全表校验必然
    # 失败且报错晦涩。带此类数据的库在入口明确拒绝：v2.5 派生归属重设计
    # （device.project_id 删列改派生等）本就不完整可逆，半途失败只会留下
    # 更难收拾的中间态。空库 / 无孤儿引用的库正常降级。
    conn = op.get_bind()
    orphan_count = conn.execute(
        text(
            "SELECT count(*) FROM plan_run pr WHERE pr.project_id IS NOT NULL "
            "AND NOT EXISTS ("
            "  SELECT 1 FROM test_project tp WHERE tp.id = pr.project_id)"
        )
    ).scalar()
    if orphan_count:
        raise RuntimeError(
            f"downgrade refused: {orphan_count} plan_run rows reference projects "
            "that no longer exist (historical GENERIC/LEGACY sentinel ids are "
            "unrecoverable after b1c2d3e4f5a6 upgrade). The v2.5 "
            "derived-ownership redesign is not fully reversible with data; "
            "restore a pre-upgrade backup instead."
        )
    op.execute(
        "INSERT INTO test_project (project_key, display_name, source, status) "
        "VALUES ('GENERIC', '通用（不限项目）', 'USER', 'ACTIVE'), "
        "('LEGACY', 'Legacy', 'SEED', 'ACTIVE')"
    )
    op.execute(
        "ALTER TABLE plan_run ADD CONSTRAINT plan_run_project_id_fkey "
        "FOREIGN KEY (project_id) REFERENCES test_project (id)"
    )
    op.execute(
        "UPDATE plan SET project_id = "
        "(SELECT id FROM test_project WHERE project_key = 'GENERIC') "
        "WHERE project_id IS NULL"
    )
    op.alter_column("plan", "project_id", nullable=False)
