# 数据模型

> **ORM**：`backend/models/`  
> **迁移**：`backend/alembic/versions/`  
> **约定**：表名单数（`device` 非 `devices`）

---

## 1. 编排与执行

### Plan / PlanStep

| 表 | 说明 |
|----|------|
| `plan` | 编排定义：`name`、`patrol_interval_seconds`、`timeout_seconds`、`next_plan_id`、`watcher_policy` |
| `plan_step` | 步骤行：`script_name`、`script_version`、`stage`(init/patrol/teardown)、`sort_order`、`enabled` |

**无** `plan.lifecycle` 列；派发时由 `plan_dispatcher_sync` 组装 `pipeline_def`。

### PlanRun

| 字段 | 说明 |
|------|------|
| `status` | RUNNING / SUCCESS / PARTIAL_SUCCESS / FAILED / DEGRADED |
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
| `task_schedule` | Cron → `plan_id` |
| `resource_pool` | WiFi 等（`connect_wifi` 注入） |

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
- 旧表已删除；勿参考 `docs/archive/stp-spec-pre-adr0020/backend/DATABASE.md`
