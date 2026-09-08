# 数据模型

> **ORM**：`backend/models/`  
> **迁移**：`backend/alembic/versions/`  
> **约定**：**新表**表名一律单数（`device` 非 `devices`）。

### 复数历史例外（#889）

以下既有表为**复数命名的历史例外**，保留以避免纯重命名迁移（重命名生产表
是高风险动作，且无正确性收益——表名是存储层事实，显式性属于模型与文档层）：

| 表 | 说明 |
|----|------|
| `users` | 初版即复数命名（`b0f805bf6cee_add_users_table`） |
| `audit_logs` | 模型 docstring 明示：匹配既有生产表，避免危险重命名迁移 |
| `notification_channels` / `alert_rules` / `notification_logs` | 通知域历史表，同批保留 |
| `device_leases` | ADR-0019 Phase 1 历史命名 |
| `task_schedules` | 历史命名（Cron → `plan_id` 调度） |

**规则**：新建表必须单数；历史复数表仅原样保留——**禁止**仅为匹配文档措辞
而重命名生产表，任何重命名必须以显式迁移（含数据回填与下游 SQL/脚本核对）
单独立项评审。

---

## 1. 编排与执行

### Plan / PlanStep

| 表 | 说明 |
|----|------|
| `plan` | 编排定义：`name`、`patrol_interval_seconds`、`timeout_seconds`、`next_plan_id`、`watcher_policy` |
| `plan_step` | 步骤行：`script_name`、`script_version`、`stage`(init/patrol/teardown)、`sort_order`、`enabled` |

**设计理由（摘要）**：自 Workflow→TaskTemplate→Pipeline 五层嵌套收敛为 **Plan→Step**（ADR-0020）。一个 Plan = 一个完整专项；`next_plan_id` 替代 Plan 内多 Block；`patrol_interval_seconds IS NULL` 表示无 patrol 阶段；步骤参数来自 Script `default_params`，不在 Plan 行存 params。

**无** `plan.lifecycle` 列；派发时由 `plan_dispatcher_sync` 组装 `pipeline_def`。

**历史迁移对照**（已归档详述 [`archive/migrations/plan-block-step-migration.md`](../archive/migrations/plan-block-step-migration.md)）：

| 旧概念 | 新概念 |
|--------|--------|
| WorkflowDefinition | Plan |
| TaskTemplate + pipeline_def JSON | PlanStep 行 |
| WorkflowRun | PlanRun |
| setup/teardown JSONB | plan_step stage=init/teardown |
| patrol JSONB.interval | plan.patrol_interval_seconds |

### PlanRun

| 字段 | 说明 |
|------|------|
| `status` | RUNNING / SUCCESS / PARTIAL_SUCCESS / FAILED / QUEUED / PRECHECK（QUEUED/PRECHECK 为准入队列状态，`STP_PLAN_ADMISSION_QUEUE_ENABLED` 默认开） |
| `plan_snapshot` | 派发时 JSON 快照 |
| `parent_plan_run_id` | Plan 链 |
| `run_type` | MANUAL / SCHEDULE / CHAIN |
| `run_context` | 含 `precheck` 等 |

### JobInstance

| 字段 | 说明 |
|------|------|
| `plan_run_id`, `plan_id` | NOT NULL |
| `device_id`, `host_id` | 扇出目标 |
| `status` | PENDING / RUNNING / COMPLETED / FAILED / ABORTED |
| `pipeline_def` | 完整 lifecycle JSON |

### StepTrace

单 Job 步骤追踪：`job_id`、`step_id`、`stage`、`status`、`original_ts`。

### JobArtifact / JobLogSignal

| 表 | 说明 |
|----|------|
| `job_artifact` | 产物元数据（AEE、快照等）；`run_log_bundle` 方案 C 后 Agent 不再注册 |
| `job_log_signal` | Watcher 异常事件流（权威异常源） |

---

## 2. 基础设施

### Host / Device

| 表 | 说明 |
|----|------|
| `host` | 字符串 PK `host-101`；`status`、心跳、`extra` JSON |
| `device` | `serial` 唯一；`host_id`；电量/温度；`lease_generation` |

### device_leases（ADR-0019）

| 字段 | 说明 |
|------|------|
| `status` | ACTIVE / RELEASED / … |
| `fencing_token` | 防旧 Agent 写 |

---

## 3. 脚本与调度

| 表 | 说明 |
|----|------|
| `script` | 脚本目录：`name`、`version`、`nfs_path`、`content_sha256`、`default_params` |
| `task_schedules` | Cron → `plan_id` |
| `resource_pool` | WiFi 等（`connect_wifi` 注入） |

### 用例集（ADR-0030 P1a）

| 表 | 说明 |
|----|------|
| `test_suite` | MTBF 用例集（≈ runtask.xml）：`name` 全局唯一、`project_id` 可空=通用套件、`export_dir`、`apk_binding`、`root_config`/`global_params`（JSON 保键序）、双漂移比对键 `source_sha256`/`exported_sha256`/`exported_content_sha256`、`is_active` |
| `test_case` | 用例（粒度 = testpoint）：`suite_id` CASCADE、`(suite_id, name)` 唯一、`ordinal`、`times`、`enabled`、`exec_descs` JSON（1..N 执行描述，标识符原样保留不「修正」） |

---

## 4. 用户与安全

| 表 | 说明 |
|----|------|
| `user` | 认证用户、`role` |
| `revoked_refresh_token` | Refresh 黑名单 jti（ADR-0024） |
| `audit_log` | 审计（ADR-0015） |

---

## 5. 通知与去重

| 表 | 说明 |
|----|------|
| `notification_channel` / `alert_rule` | 告警 |
| `plan_run_artifact` | PlanRun 级 dedup xls 等（Sprint 4 扩展） |

---

## 6. Agent 本地（SQLite）

`backend/agent/registry/local_db.py`：

| 逻辑表 | 说明 |
|--------|------|
| `log_signal_outbox` | 离线重试 |
| `watcher_state` | Watcher 游标 |
| job_archive 等 | 本地归档队列（方案 C 后简化） |

---

## 7. 枚举单一源

`backend/models/enums.py` — 前后端状态值应对齐 `frontend/src/utils/api/types.ts`。

---

## 8. 关系简图

```
Plan 1──* PlanStep
Plan 1──* PlanRun
PlanRun 1──* JobInstance
JobInstance 1──* StepTrace
JobInstance 1──* JobArtifact
JobInstance 1──* JobLogSignal
Host 1──* Device
Device 1──* device_leases
```

---

## 9. 历史迁移

- ADR-0020 一次性迁移：Workflow* → Plan*（见 `plan_migration_audit`）  
- 旧表已删除；设计理由见 [`archive/migrations/plan-step-design-rationale.md`](../archive/migrations/plan-step-design-rationale.md)  
- 勿参考 `docs/archive/stp-spec-pre-adr0020/backend/DATABASE.md`
