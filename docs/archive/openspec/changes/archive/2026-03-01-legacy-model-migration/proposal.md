# Proposal: legacy-model-migration

**Status**: Research Complete
**Date**: 2026-02-28

---

## Context

项目中 `backend/models/schemas.py`（旧 ORM）和 `backend/models/host.py`（新 STP ORM）同时向 SQLAlchemy 同一 `Base` 注册了两个名为 `Device` 的类，导致 mapper 配置冲突，引发 `GET /api/v1/agent/runs/pending` 500 崩溃。

根本原因：Phase 1（task-orchestration-concept-map）引入了新 STP 模型层，但旧 `schemas.py` ORM 类未被清除，两套 ORM 并存引发命名歧义。

---

## User Decisions

| 决策项 | 结果 |
|---|---|
| Host PK 策略 | **重命名+数据迁移**（hosts→host，Integer PK→String，str(old_id) 映射） |
| Agent 端点 | **同步迁移**（agent/main.py 切换到 agent_api.py，旧 tasks.py Agent 回调删除） |
| 保留功能 | User/Auth（JWT）、TaskSchedule（定时调度）、NotificationChannel/AlertRule、LogArtifact |
| 废弃功能 | Deployment 模块、报告生成（report_json/JIRA）、旧 Workflow/WorkflowStep、DeviceMetricSnapshot |

---

## Constraint Sets

### Hard Constraints

**HC-1**: Host PK 从 Integer 迁移到 String(64)，所有 FK 引用列同步变更（Device.host_id, HeartbeatIn.host_id, api/schemas.py HostOut.id）。

**HC-2**: Agent `main.py` 5个旧端点在同步切换前不可删除，新旧端点需同时存活直至新 Agent 部署确认。

**HC-3**: 新 `host.py::Device` 缺少 18 个监控字段，`heartbeat.py` 和 `recycler.py` 依赖这些字段，迁移前必须在新模型中补齐。

**HC-4**: `recycler.py` 裸 SQL 硬编码表名 `devices`，表名改为 `device` 后必须同步更新。

**HC-5**: `RunStatus` 枚举与 `JobStatus` 枚举不兼容，历史数据迁移需逐行状态转换。

**HC-6**: 旧 SQLAlchemy `Enum` 列在 PostgreSQL 存储为 enum 类型，Alembic 需显式 `USING CAST(...::text)` 迁移到 String。

**HC-7**: 新 `Device.tags` 使用 `JSONB`，旧 `tags` 过滤（LIKE）需更新为 JSONB 运算符 `@>`。

### Soft Constraints

**SC-1**: 所有新端点沿用 `ApiResponse[T]` 统一响应格式。

**SC-2**: REST API 路径保持不变（仅内部 ORM 切换）。

**SC-3**: Pydantic schemas（`api/schemas.py`）保留，仅调整字段类型。

**SC-4**: Auth 体系（User + JWT）完整保留，新模型中 `triggered_by` 字段写入用户名字符串而非 FK。

### Dependency Order

```
Phase A（紧急修复，立即）
  → 不依赖数据库迁移，仅修改 import 路径

Phase B（新 STP 模型字段补齐）
  → 必须先于 Phase C 执行

Phase C（Alembic 数据库迁移）
  → 必须先于 Phase D/E 执行

Phase D（路由层迁移：heartbeat/hosts/devices）
Phase E（调度器迁移：TaskDispatcher/Recycler/CronScheduler）
  → D、E 可并行，均依赖 Phase C

Phase F（Agent 端点切换 + agent/main.py 重写 + 部署）
  → 必须先于 Phase G

Phase G（旧 ORM 清理：删除 schemas.py 旧类）
  → 所有路由/调度器切换完成后执行
```

---

## Scope

### 修改文件

| 文件 | 改动 |
|---|---|
| `backend/models/host.py` | 补齐 Device 18个监控字段 + Host ssh_*/extra/mount_status |
| `backend/api/routes/orchestration.py` | 改用 `host.py::Device`（Phase A 立即） |
| `backend/api/routes/heartbeat.py` | 切换到新 Host/Device ORM |
| `backend/api/routes/hosts.py` | 切换到新 Host ORM |
| `backend/api/routes/devices.py` | 切换到新 Device ORM + JSONB |
| `backend/api/schemas.py` | HostOut.id/DeviceOut.host_id/HeartbeatIn.host_id 改为 str |
| `backend/scheduler/dispatcher.py` | 适配新 Device 锁字段 |
| `backend/scheduler/recycler.py` | 裸 SQL 表名修正 + 适配新模型 |
| `backend/scheduler/cron_scheduler.py` | 触发 WorkflowRun 而非创建 Task |
| `backend/agent/main.py` | 切换到 agent_api.py 新端点 |
| `backend/alembic/versions/` | 新迁移脚本 |

### 废弃/删除

| 目标 | 原因 |
|---|---|
| `schemas.py::Deployment / DeploymentStatus` | 无活跃路由 |
| `schemas.py::Workflow / WorkflowStep` | 新 WorkflowDefinition 已替代 |
| `schemas.py::DeviceMetricSnapshot` | 废弃，时序数据由 Redis 承接 |
| `schemas.py::Device / Host`（Phase G） | 迁移完成后删除 |
| `backend/scheduler/workflow_executor.py` | 驱动旧 Workflow/WorkflowStep |
| `backend/api/routes/deploy.py` | Deployment 路由废弃 |

### 不在范围

- `backend/api/routes/auth.py`（保留）
- `backend/api/routes/notifications.py`（保留）
- `backend/models/job.py / workflow.py / tool.py`（不改动）
- 前端（路径不变，不改动）

---

## Success Criteria

**SC-1**: `GET /api/v1/agent/runs/pending` 恢复正常响应。

**SC-2**: Python import 任意后端模块不出现 `Multiple classes found for path "Device"` 错误。

**SC-3**: `POST /api/v1/heartbeat`、`GET /api/v1/hosts`、`GET /api/v1/devices` 功能等价切换。

**SC-4**: Alembic `upgrade head` 成功，历史 Host/Device 数据在新表中可查。

**SC-5**: Linux Agent 重新部署后能正常领取 JobInstance，不再调用旧端点。

**SC-6**: `schemas.py` 不再包含 `class Device(Base)` 或 `class Host(Base)`，项目正常启动。

---

## Risk Mitigation

| 风险 | 缓解策略 |
|---|---|
| Host PK 类型变更级联破坏 | str(old_int_id) 保留数值语义，Alembic 脚本填充 |
| Agent 切换窗口期任务悬挂 | 新旧端点同时存活至新 Agent 部署确认后删除 |
| recycler.py 裸 SQL 失效 | Phase B 同步更新表名和字段名 |
| DeviceMetricSnapshot 历史数据丢失 | 废弃前 pg_dump 归档 |
| 通知服务触发点缺失 | aggregator.py on_job_terminal 补充通知调用 |
