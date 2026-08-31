"""schema 基线收敛（#644 后续）——空库 upgrade 与生产历史分叉对齐到模型。

背景：001 基线迁移的 ``if not _table_exists`` 条件 + 生产历史演进造成
空库与生产 schema 分叉，schema-sync 基线里 20+ 项噪音大部分源于此
（旧表 devices/hosts、host.hostname 约束、job_instance 部分唯一索引、
host/action_template/resource_allocation/notification_logs 索引、
jira_run 唯一约束、plan.specialty_id NOT NULL）。本迁移把两端都拉到
模型口径；每步幂等（IF EXISTS / IF NOT EXISTS / DO 块），对空库与生产
各自安全，已存在即跳过。

Revision ID: g7h8i9j0k1l2
Revises: f7g8h9i0j1k2
Create Date: 2026-08-31
"""

from alembic import op

revision = "g7h8i9j0k1l2"
down_revision = "e7f8a9b0c1d2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1) 旧遗留表（001 基线建的复数表，模型无对应；生产已清理，空库清理。
    #    CASCADE：tasks/task_runs 等早期表若引用它们，随表一并删除——这些
    #    旧表整体不在模型里，无保留价值）
    op.execute("DROP TABLE IF EXISTS devices, hosts CASCADE")
    # 2) host.hostname 唯一约束（空库缺——001 建表带列但无约束）
    op.execute(
        "DO $$ BEGIN IF NOT EXISTS ("
        "SELECT 1 FROM pg_constraint WHERE conname='host_hostname_key' "
        "AND conrelid='host'::regclass"
        ") THEN ALTER TABLE host ADD CONSTRAINT host_hostname_key UNIQUE (hostname); "
        "END IF; END $$"
    )
    # 3) host.last_heartbeat 索引（生产缺）
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_host_last_heartbeat "
        "ON host (last_heartbeat)"
    )
    # 4) uq_job_active_per_device（模型声明、空库已有；生产缺——补建）
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_job_active_per_device "
        "ON job_instance (device_id) "
        "WHERE status IN ('PENDING', 'RUNNING', 'UNKNOWN')"
    )
    # 5) action_template 活跃索引（生产缺）
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_action_template_active "
        "ON action_template (is_active, name)"
    )
    # 6) resource_allocation 两索引（生产缺）
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_resource_allocation_job "
        "ON resource_allocation (job_instance_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_resource_allocation_pool "
        "ON resource_allocation (resource_pool_id)"
    )
    # 7) notification_logs 两索引（生产缺）
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_notification_logs_created_at "
        "ON notification_logs (created_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_notification_logs_read "
        "ON notification_logs (read)"
    )
    # 8) jira_run.console_run_id 唯一约束（生产缺；模型显式命名对齐）
    op.execute(
        "DO $$ BEGIN IF NOT EXISTS ("
        "SELECT 1 FROM pg_constraint WHERE conname='uq_jira_run_console_run_id' "
        "AND conrelid='jira_run'::regclass"
        ") THEN ALTER TABLE jira_run "
        "ADD CONSTRAINT uq_jira_run_console_run_id UNIQUE (console_run_id); "
        "END IF; END $$"
    )
    # 9) plan.specialty_id DROP NOT NULL——DB 向模型对齐（specialty 必填是
    #    应用层 PlanCreate schema 校验；e6f7g8h9i0j1 迁移的 DB 层 NOT NULL
    #    是增强而非契约，模型一直 nullable=True，create_all 测试库亦如是）
    op.execute("ALTER TABLE plan ALTER COLUMN specialty_id DROP NOT NULL")


def downgrade() -> None:
    op.execute("ALTER TABLE plan ALTER COLUMN specialty_id SET NOT NULL")
    op.execute("DROP INDEX IF EXISTS ix_notification_logs_read")
    op.execute("DROP INDEX IF EXISTS ix_notification_logs_created_at")
    op.execute("DROP INDEX IF EXISTS ix_resource_allocation_pool")
    op.execute("DROP INDEX IF EXISTS ix_resource_allocation_job")
    op.execute("DROP INDEX IF EXISTS ix_action_template_active")
    op.execute("DROP INDEX IF EXISTS uq_job_active_per_device")
    op.execute("DROP INDEX IF EXISTS idx_host_last_heartbeat")
    op.execute(
        "ALTER TABLE host DROP CONSTRAINT IF EXISTS host_hostname_key"
    )
