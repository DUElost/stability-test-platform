-- PostgreSQL 性能优化配置
-- 此脚本在数据库初始化时执行

-- 创建优化索引
CREATE INDEX IF NOT EXISTS idx_devices_status_lock ON devices (status, lock_run_id, lock_expires_at) WHERE status = 'ONLINE';
CREATE INDEX IF NOT EXISTS idx_tasks_status_priority ON tasks (status, priority, created_at) WHERE status = 'PENDING';
CREATE INDEX IF NOT EXISTS idx_task_runs_status_host ON task_runs (status, host_id) WHERE status IN ('QUEUED', 'DISPATCHED', 'RUNNING');
CREATE INDEX IF NOT EXISTS idx_hosts_status_heartbeat ON hosts (status, last_heartbeat) WHERE status = 'ONLINE';

-- 设置时区
SET TIME ZONE 'UTC';

-- 日志记录
DO $$
BEGIN
    RAISE NOTICE 'PostgreSQL optimization indexes created successfully';
END $$;
