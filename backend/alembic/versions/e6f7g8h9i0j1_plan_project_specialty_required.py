"""ADR-0029 P1-B2 — Plan 归属/专项双必填 + GENERIC 哨兵 + ops 词条。

- specialty 新增 ops（运维型）词条——PlanListPage 的 specialty 筛选器
  从「永远筛不出东西」变成可用
- GENERIC 项目（「通用（不限项目）」）：运维型 Plan（刷机/装 APK）的显式
  哨兵——NULL 不再存在，「不归属」必须显式表达
- 存量 9 行回填：project_id IS NULL → GENERIC；specialty 按名称关键词
  推断（mtbf/monkey/power-cycle，其余 ops）
- 回填后 plan.project_id / plan.specialty_id 收 NOT NULL

Revision ID: e6f7g8h9i0j1
Revises: d5e6f7g8h9i0
Create Date: 2026-08-30
"""

from alembic import op
import sqlalchemy as sa

revision = "e6f7g8h9i0j1"
down_revision = "d5e6f7g8h9i0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "INSERT INTO specialty (key, display_name, sort_order) "
        "VALUES ('ops', '运维', 10) ON CONFLICT (key) DO NOTHING"
    )
    op.execute(
        sa.text(
            """
            INSERT INTO test_project (
                project_key, display_name, customer, form_factor,
                product_line, jira_project_key, status, source,
                match_models, created_at, updated_at
            )
            SELECT 'GENERIC', '通用（不限项目）', NULL, NULL, NULL, NULL,
                   'ACTIVE', 'USER', '[]', now(), now()
            WHERE NOT EXISTS (
                SELECT 1 FROM test_project WHERE project_key = 'GENERIC'
            )
            """
        )
    )
    # 存量回填：未归属 Plan → GENERIC（显式「不限」哨兵）
    op.execute(
        sa.text(
            """
            UPDATE plan SET project_id = (
                SELECT id FROM test_project WHERE project_key = 'GENERIC'
            )
            WHERE project_id IS NULL
            """
        )
    )
    # 存量回填：specialty 按名称关键词推断
    op.execute(
        sa.text(
            """
            UPDATE plan SET specialty_id = CASE
                WHEN name ILIKE '%mtbf%' THEN
                    (SELECT id FROM specialty WHERE key = 'mtbf')
                WHEN name ILIKE '%monkey%' THEN
                    (SELECT id FROM specialty WHERE key = 'monkey')
                WHEN name ILIKE '%power%' THEN
                    (SELECT id FROM specialty WHERE key = 'power-cycle')
                ELSE (SELECT id FROM specialty WHERE key = 'ops')
            END
            WHERE specialty_id IS NULL
            """
        )
    )
    op.alter_column("plan", "project_id", nullable=False)
    op.alter_column("plan", "specialty_id", nullable=False)


def downgrade() -> None:
    op.alter_column("plan", "specialty_id", nullable=True)
    op.alter_column("plan", "project_id", nullable=True)
    op.execute("DELETE FROM test_project WHERE project_key = 'GENERIC'")
    op.execute("DELETE FROM specialty WHERE key = 'ops'")
