# Migration Preflight — k9f0a1b2c3d4_add_watcher_lifecycle_fields + m1g2h3i4j5k6_add_job_active_per_device_unique

**执行任一 `alembic upgrade` 之前完成本清单。**

> 🔀 **2026-04-18 migration 拆分**
> 原 `k9f0a1b2c3d4` 混合了列/表添加 + partial unique index；为了让 watcher 铺路治理 PR 可以安全 upgrade，现已拆分：
> - **`k9f0a1b2c3d4`**：列 + 表 + 普通索引（零脏数据风险；治理 PR 随 PR 合入时 upgrade）
> - **`m1g2h3i4j5k6`**：partial unique index `uq_job_active_per_device`（watcher MVP PR 上线前手动推进，本文件 §1.1 为硬前置条件）
>
> 本 PREFLIGHT 同时覆盖两个 migration，§1.1 仅针对 `m1g2h3i4j5k6`。

---

## 1. 脏数据扫描（开发 / 预发 / 生产逐环境执行）

### 1.1 同设备多活跃 Job 检测（阻断项）

```sql
-- 连到目标库后执行；返回非空即为阻断项
SELECT device_id,
       array_agg(id)     AS job_ids,
       array_agg(status) AS statuses,
       count(*)          AS active_count
  FROM job_instance
 WHERE status IN ('PENDING', 'RUNNING', 'UNKNOWN')
 GROUP BY device_id
HAVING count(*) > 1
 ORDER BY active_count DESC;
```

**预期**：0 行。若非 0：

- **同一 workflow_run 扇出重复**：属于 dispatcher bug，走业务核查
- **历史 UNKNOWN 堆积**：用下面 SQL 把 UNKNOWN + ended_at 已过半小时的 Job 归档为 ABORTED
  ```sql
  UPDATE job_instance
     SET status = 'ABORTED',
         status_reason = COALESCE(status_reason, '') || ' [preflight:stale_unknown_archived]',
         ended_at = now()
   WHERE status = 'UNKNOWN'
     AND updated_at < now() - interval '30 minutes';
  ```
- **真实并发冲突**：由运维评估是否强制置 ABORTED 释放设备锁

> ⚠️ 任何 UPDATE 前必须先 `BEGIN;` + 复核预计影响行数，再 `COMMIT;`。

### 1.2 `device.lock_run_id` 悬挂指针检测（诊断项）

```sql
-- lock_run_id 指向的 Job 已经不是活跃状态（说明设备锁泄漏）
SELECT d.id     AS device_id,
       d.serial,
       d.lock_run_id,
       j.status AS lock_job_status
  FROM device d
  LEFT JOIN job_instance j ON j.id = d.lock_run_id
 WHERE d.lock_run_id IS NOT NULL
   AND (j.id IS NULL OR j.status NOT IN ('PENDING','RUNNING','UNKNOWN'));
```

**非阻断**，但建议清理：

```sql
-- 清理悬挂锁（确认上表结果合理后执行）
UPDATE device
   SET lock_run_id = NULL,
       lock_expires_at = NULL,
       status = 'ONLINE'
 WHERE lock_run_id IS NOT NULL
   AND lock_run_id NOT IN (
       SELECT id FROM job_instance
        WHERE status IN ('PENDING','RUNNING','UNKNOWN')
   );
```

### 1.3 JSONB 列已有数据检测（诊断项）

```sql
-- 确认 workflow_definition 表中没人提前把 watcher_policy 当普通 JSON 列用
SELECT column_name, data_type FROM information_schema.columns
 WHERE table_name = 'workflow_definition' AND column_name = 'watcher_policy';
-- 预期：0 行（未迁移前不应存在）
```

---

## 2. ORM 同步清单（必须与 migration 同 PR）

迁移执行后，下列文件**必须**同步更新并在 `backend/main.py` import 路径中生效，否则 SQLAlchemy 启动即报错：

| 文件 | 变更 | 本骨架的 diff 引用 |
|---|---|---|
| `backend/models/workflow.py` | `WorkflowDefinition` 加 `watcher_policy` 列 | §4.1 |
| `backend/models/job.py` | `JobInstance` 加 4 个 watcher_* 列 + `log_signals` relationship | §4.2 |
| `backend/models/job.py` | 新增 `JobLogSignal` ORM 类 | §4.2 |
| `backend/models/__init__.py` | 导出 `JobLogSignal`（若沿用集中导出） | 按项目约定 |

**验证**：应用迁移 + 改 ORM 后，跑 `pytest backend/ -x -q` 至少要全通过（尤其关注 `test_agent.py` 和 workflow 相关单测）。

---

## 3. Agent 侧前置条件

| 条件 | 校验方式 |
|---|---|
| Agent 版本已打包本 PR 的 watcher/ + job_session | `grep -r "JobSession" /opt/stability-test-agent/` |
| LocalDB schema 已包含 `log_signal_outbox` + `watcher_state` | Agent 启动日志中能看到 "LocalDB initialized" 且表创建不报错 |
| `/agent/jobs/claim` 响应已按契约返回 `device_serial` + `host_id` | 用 curl/postman 对一个真实 claim 断言字段存在 |

**重要**：这三项未满足时，Agent 不可升级。schema 先于代码升级 OK（新列允许 NULL + server_default）；**代码升级晚于 schema 时会读到未知列是安全的**。

---

## 4. 回滚策略

### 4.1 正常回滚

```bash
alembic downgrade -1
```

downgrade 会按相反顺序删掉 partial index → 新表 → 新列。**前置条件**：`job_log_signal` 表可被 drop（没有外部进程在读写）。

### 4.2 回滚失败的补救

若 downgrade 中途失败（例如 partial index 已删但 drop_table 因 FK 卡住）：

```sql
-- 手工按顺序清理
DROP INDEX IF EXISTS uq_job_active_per_device;
DROP INDEX IF EXISTS idx_job_log_signal_detected;
DROP INDEX IF EXISTS idx_job_log_signal_category;
DROP INDEX IF EXISTS idx_job_log_signal_job;
DROP TABLE IF EXISTS job_log_signal;
ALTER TABLE job_instance
  DROP COLUMN IF EXISTS log_signal_count,
  DROP COLUMN IF EXISTS watcher_capability,
  DROP COLUMN IF EXISTS watcher_stopped_at,
  DROP COLUMN IF EXISTS watcher_started_at;
ALTER TABLE workflow_definition DROP COLUMN IF EXISTS watcher_policy;

-- 最后把 alembic_version 手工拉回
UPDATE alembic_version SET version_num = 'j8e9f0a1b2c3';
```

### 4.3 数据修复回滚（Agent 已产生 log_signal）

如果 Agent 已经写入了 `job_log_signal` 数据，downgrade 会 DROP TABLE 丢失数据：

```bash
# 回滚前先导出
psql $DATABASE_URL -c "\copy job_log_signal TO '/tmp/job_log_signal_backup.csv' WITH CSV HEADER"
```

---

## 5. 执行顺序（推荐）

### 5.1 治理 PR 阶段（仅推进 k9f0a1b2c3d4）

```
[ ] 1. 备份 job_instance + device + workflow_definition 表
[ ] 2. 合并 ORM 同步 PR（§2）
[ ] 3. alembic upgrade k9f0a1b2c3d4
[ ] 4. 验证应用层启动（FastAPI 无异常，Swagger 能开）
[ ] 5. 观察 Agent 回传 watcher_summary / log_signals（feature flag 开启的单 host）
```

### 5.2 Watcher MVP 上线阶段（推进 m1g2h3i4j5k6）

```
[ ] 1. 冻结 Agent 版本（不要有新 Agent 连上）
[ ] 2. 执行 §1.1 扫描 — 若非空，完成业务治理
[ ] 3. 执行 §1.2 扫描 — 清理悬挂锁
[ ] 4. 备份 job_instance 表
[ ] 5. alembic upgrade m1g2h3i4j5k6
[ ] 6. 验证应用层启动 + partial unique index 已存在
[ ] 7. 灰度放开 Agent 升级（单 host 先升，观察 1h）
[ ] 8. 全量升级 Agent
```

§5.2 的步骤 2-3 任一 SQL 结果非空且无法解释时，**不得**执行步骤 5。

---

## 6. 应急联系人（按项目约定填写）

- DBA 联系人: _
- 值班运维: _
- 本迁移作者: _
