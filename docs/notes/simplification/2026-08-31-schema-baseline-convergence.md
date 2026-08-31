# schema-sync 基线收敛（41 → 5 项）+ 模型↔DB 对齐

日期：2026-08-31 · 类型：simplification · 关联：#644 schema-sync 守卫 · PR：#664

## 决定了什么

schema-sync 基线 41 项噪音收敛到 5 项（全部为 alembic 固有比较噪音，
无法收敛），20+ 项通过「模型对齐 DB」或「DB 对齐模型」消除：

**DB → 模型对齐（新迁移 g7h8i9j0k1l2，幂等）**
- DROP 旧遗留表 `devices`/`hosts`（001 基线迁移建的复数表，生产早已
  手工清理、模型无对应；空库 CASCADE 清理）
- 空库补 `host_hostname_key` 唯一约束（001 建表带列无约束）
- 生产补 `idx_host_last_heartbeat` / `ix_action_template_active` /
  `ix_resource_allocation_{job,pool}` / `ix_notification_logs_{created_at,read}`
  / `uq_job_active_per_device`（模型声明、生产缺失）
- 生产补 `uq_jira_run_console_run_id` 唯一约束
- `plan.specialty_id` DROP NOT NULL——DB 向模型对齐（specialty 必填是
  应用层 PlanCreate 校验；DB 层 NOT NULL 是 e6f7g8h9i0j1 增强而非契约）

**模型 → DB 对齐（无迁移）**
- host_id FK ×6 补 `ondelete/onupdate CASCADE`（与 DB 一致）
- `plan_run.project_id` **删 FK**——v2.5 M4 后 DB 本就无此 FK（悬空快照
  设计），模型是唯一残留声明；relationship 显式 primaryjoin 保持 ORM
- 索引/约束显式命名：hostname `host_hostname_key`、jira_run
  `uq_jira_run_console_run_id` + unique 索引、ai_assistant_action
  `ix_ai_assistant_action_session`（对齐迁移链命名，消除自动命名漂移）
- 补索引声明：action_template / resource_allocation ×2 /
  notification_logs ×2（+ created_at 补 timezone）
- **`--rebaseline` 改覆盖语义**（原并集会让已修复项永久留在基线）

**保留的 5 项（alembic 固有噪音）**：PG enum 比较 ×2（alert_rules /
notification_channels，模型 Enum vs DB 原生 enum）、JSON vs JSONB ×1
（jira_run.issue_keys，模型 JSON 为 SQLite 测试兼容）、partial+ops
索引文本比较 ×2（idx_plan_run_admission_queue，模型带 cast 会破坏
SQLite）。

## 放弃的备选

- **plan.specialty_id 模型改 nullable=False**：117 处测试 Plan 构造点全要
  补 specialty_id，改动面不可接受；反转方向（DB DROP NOT NULL）零风险
- **admission_queue 模型对齐 DB 文本**（`::plan_run_status` cast）：SQLite
  create_all 不支持 PG cast 语法，破坏测试库

## 如何验证

- 空库 upgrade head → schema-sync 通过（5 项全在基线）；基线 41 → 5
- 测试 708 例（project_routes/devices/dispatcher/results/dedup/services）
  全过——模型改动对 create_all 测试库无行为影响
- 生产部署：迁移应用后 schema-sync 生产跑（预期仍有个别环境差异项，
  见下）+ backend restart

## 何时重议

- PG enum / JSONB 噪音在 alembic 修复后自动消失（届时重刷基线）
- 生产历史差异（revoked_refresh_token 孤儿表等）另案清理后可进一步缩水
