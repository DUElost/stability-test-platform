"""ADR-0029 v2.5 D11 M4 — plan.project_id 恢复可空 + 删 GENERIC/LEGACY 哨兵。

显式性属于 API/UI 契约（UI 二选一单选），存储层 NULL = 不限——P1-B2
收 NOT NULL 的动机对但放错了层。plan_run.project_id 是历史快照（v2.5
保留），其 FK 引用会阻止删哨兵行——drop FK（快照本就不该引用活表）。

Revision ID: b1c2d3e4f5a6
Revises: l5m6n7o8p9q0
Create Date: 2026-08-31
"""

from alembic import op

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
