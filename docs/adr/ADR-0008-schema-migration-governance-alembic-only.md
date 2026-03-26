# ADR-0008: 统一 Schema 迁移治理（Alembic Only）
- 状态：Accepted
- 优先级：P0
- 目标里程碑：M1
- 日期：2026-02-18
- 接受日期：2026-03-24
- 决策者：平台研发组
- 标签：数据库迁移, Alembic, 无畏重构

## 背景

当前存在三套并行行为：

- `Base.metadata.create_all`
- 启动时运行时 `ALTER TABLE` 补列
- Alembic 版本迁移

这会导致环境间 Schema 漂移和回溯困难，不利于持续演进。

## 决策

迁移治理统一为 Alembic 主导：

- 禁止在 `main.py` 中新增运行时 DDL。
- 禁止依赖 `create_all` 自动演进生产 Schema。
- 所有结构变更通过 Alembic 脚本管理并可回滚。
- 启动阶段仅做"版本检查与告警"，不做结构写入。

## 备选方案与权衡

- 方案 A：保持现状（多通道并存）。
  - 优点：短期改动少。
  - 缺点：长期数据一致性风险高。
- 方案 B：一次性强制切换 Alembic。
  - 优点：治理清晰。
  - 缺点：需要梳理历史差异并补齐迁移脚本。

## 影响

- 正向影响：Schema 可追溯、可审计、可回滚。
- 代价：迁移脚本编写成本上升，CI 需要增加迁移校验。

## 落地与后续动作

| 步骤 | 内容 | 状态 | 备注 |
|------|------|------|------|
| 第一步 | 冻结运行时 DDL 新增 | **已完成** | `main.py` 已移除 `create_all` 和 `ALTER TABLE`（commit 6befa34） |
| 第二步 | 补齐现有表结构到 Alembic 版本 | **部分完成** | 7 个迁移文件已创建；recycler + report_service 已迁移新模型（2026-03-24）; post_completion 待列新增 |
| 第三步 | CI 增加"迁移后模型一致性检查" | 未实现 | 依赖双轨合并完成后方可落地（见下文） |

## 已知问题：ORM 模型双轨并行

> **发现日期**：2026-03-24
> **严重性**：阻塞 — Phase 1 迁移不可安全执行，CI 一致性检查无法通过

### 现状描述

应用代码同时使用两套 ORM 模型，指向不同命名约定的表：

**新模型（单数表名）** — Phase 1 迁移 `a1b2c3d4e5f6` 创建：

| 模型文件 | ORM 类 | `__tablename__` |
|----------|--------|-----------------|
| `models/host.py` | `Host`, `Device` | `host`, `device` |
| `models/job.py` | `TaskTemplate`, `JobInstance`, `StepTrace` | `task_template`, `job_instance`, `step_trace` |
| `models/workflow.py` | `WorkflowDefinition`, `WorkflowRun` | `workflow_definition`, `workflow_run` |
| `models/tool.py` | `Tool` | `tool` |
| `models/action_template.py` | `ActionTemplate` | `action_template` |

**旧模型（复数表名）** — 仍被 20+ 个文件活跃引用，但其对应表在 Phase 1 迁移中被 DROP：

| ORM 类（`models/schemas.py`） | `__tablename__` | 活跃引用文件数（后端） |
|-------------------------------|-----------------|------------------------|
| `Task` | `tasks` | 7+ |
| `TaskRun` | `task_runs` | 3+ |
| `RunStep` | `run_steps` | 1+ |
| `LogArtifact` | `log_artifacts` | 1 |
| `Tool`（schemas 版） | `tools` | 4 |
| `ToolCategory` | `tool_categories` | 4 |
| `AuditLog` | `audit_logs` | 2 |
| `TaskTemplate`（schemas 版） | `task_templates` | 1+ |

> **前端引用补充**：上表仅统计后端 `from backend.models.schemas import` 的直接引用。前端另有约 19 个文件引用了 `tasks` / `task_runs` 相关的 API 类型定义（如 `api.ts` 中的 `TaskRun` 接口、`TaskDetails.tsx` 等页面组件）。虽然前端不直接依赖 ORM 模型，但双轨合并后 API 响应结构（字段名、嵌套关系）可能随之变化，前端类型定义与消费逻辑需同步适配。此项应纳入步骤 2「逐模型迁移」的检查清单。

**未受影响的模型**（`schemas.py` 中表未被 Phase 1 删除）：

| ORM 类 | `__tablename__` | 说明 |
|--------|-----------------|------|
| `User` | `users` | Phase 1 未 DROP |
| `NotificationChannel` | `notification_channels` | Phase 1 未 DROP |
| `AlertRule` | `alert_rules` | Phase 1 未 DROP |
| `TaskSchedule` | `task_schedules` | Phase 1 DROP 后由 `e2f3a4b5c6d7` 重建 |

### 冻结决策

**迁移 `a1b2c3d4e5f6_add_stp_spec_phase1_schema.py` 标记为冻结（FROZEN）**，不得在生产环境执行。

原因：该迁移 DROP 的 17 张旧表仍有 ORM 模型被应用代码活跃引用，执行将导致应用崩溃。

当前数据库实际停留在 Phase 1 之前的状态（新旧表共存，由历史 `create_all` 创建）。Alembic `current` 版本应在 `c1a2b3d4e5f6` 或更早。

### 后续工作项：双轨合并

**目标**：将所有旧模型引用迁移到新模型，然后安全执行 Phase 1 迁移。

**步骤**：

1. **盘点**：逐文件梳理 `schemas.py` 旧模型的所有 import 位置（已完成，见上表）
2. **逐模型迁移**：按依赖顺序将旧模型引用替换为新模型
   - `Task` / `TaskRun` / `RunStep` → `JobInstance` / `StepTrace`（需适配字段差异）
   - `Tool`（schemas 版）/ `ToolCategory` → `Tool`（tool.py 版）
   - `LogArtifact` → 评估是否需要新表或合并到 `StepTrace`
   - `AuditLog` → 在新 schema 中补建或保留
3. **清理 `schemas.py`**：移除已废弃模型，仅保留 `User`、`NotificationChannel`、`AlertRule`、`TaskSchedule` 及枚举
4. **重写 Phase 1 为渐进式迁移**：确认无旧模型引用后，将冻结的 `a1b2c3d4e5f6` 拆分为多步渐进式迁移脚本（逐表创建 → 数据搬迁 → 旧表 DROP），避免单次大迁移的回滚风险
5. **CI 一致性检查**：双轨合并后实施（见第三步）

**建议优先级**：可纳入 M2 里程碑，与 ADR-0010/0011 新表需求一并规划。

## Wave 3a 已完成项（2026-03-24）

### Recycler 迁移至 JobInstance

`backend/scheduler/recycler.py` 已完全重写，移除所有 `Task`/`TaskRun`/`LogArtifact` 依赖：

| 变更项 | 旧实现 | 新实现 |
|--------|--------|--------|
| 超时对象 | `TaskRun` (DISPATCHED/RUNNING) | `JobInstance` (PENDING/RUNNING) |
| 状态转换 | 直接 `run.status = FAILED` | `JobStateMachine.transition()` 含两步转换 |
| 设备锁释放 | `release_lock_sync(db, device_id, run.id)` | 同（run.id → job.id） |
| Host/Device 超时 | recycler 自行检查（可选跳过） | 完全移除，由 session_watchdog 独占处理 |
| Artifact 清理 | 删除 `LogArtifact` 行 + 物理文件 | 仅删除物理文件（StepTrace 行为审计记录，不删除） |
| Post-completion | 调用 `run_post_completion_async` | **暂不调用**（见下文） |

Recycler 已在 `main.py` 启用（`start_recycler()`），与 session_watchdog 并行运行，查询的 status 值不重叠。

### Report Service 脱离旧模型

`backend/services/report_service.py` 已移除 `TaskRun` fallback 路径：

- 删除 `from backend.models.schemas import LogArtifact, Task, TaskRun`
- `compose_run_report()` 仅走 `JobInstance` → `_compose_job_report()` 路径
- `_load_risk_summary_from_artifacts()` 签名从 `List[LogArtifact]` 改为 `list`（duck-typing）

### Orchestration 端点预留

`backend/api/routes/orchestration.py` 新增 3 个 501 stub 端点：

| 端点 | 用途 | 替代的旧端点 |
|------|------|-------------|
| `GET /workflow-runs/{run_id}/jobs/{job_id}/report` | 单 Job 报告 | `GET /runs/{run_id}/report` |
| `POST /workflow-runs/{run_id}/jobs/{job_id}/jira-draft` | Job JIRA 草稿 | `POST /runs/{run_id}/jira-draft` |
| `GET /workflow-runs/{run_id}/summary` | Workflow 聚合概览 | 无（新增） |

旧端点暂时保留作为 interim 通道，待新端点实现后移除。

## Post-Completion Pipeline 迁移（延后）

> **状态**：已规划，待 Alembic 迁移
> **阻塞项**：`job_instance` 表缺少报告缓存列

### 现状

`backend/services/post_completion.py` 在 run 完成后自动生成 report + JIRA draft 并缓存到 `TaskRun` 行：

```python
run.report_json = report.model_dump(mode="json")
run.jira_draft_json = jira_draft.model_dump(mode="json")
run.post_processed_at = datetime.utcnow()
```

`JobInstance` 模型（`backend/models/job.py`）无对应列，因此：
- Recycler 的 `_mark_timeout()` 不再调用 `run_post_completion_async()`
- `agent_api.py` 的 `complete_job()` 未调用 post-completion
- Post-completion 对新模型路径为无效代码

### 方案评估

| 方案 | 描述 | 优劣 |
|------|------|------|
| **A. 为 `job_instance` 加 3 列（推荐）** | `report_json JSONB`, `jira_draft_json JSONB`, `post_processed_at TIMESTAMPTZ` | 最直接，与旧模型对齐，Alembic 一步完成 |
| B. 存入 `WorkflowRun.result_summary` | 将 per-job 报告塞进 workflow 级 JSONB | 语义混淆，per-job vs per-workflow 不分 |
| C. 新建 `job_report_cache` 表 | 独立缓存表 | 过度设计，增加 JOIN 复杂度 |
| D. 存为 StepTrace 事件 | `event_type="REPORT_CACHED"` | 语义超载，StepTrace 应为审计记录 |

### 实施步骤（后续工作项）

1. 创建 Alembic 迁移脚本：`job_instance` 表新增 `report_json`、`jira_draft_json`、`post_processed_at` 列
2. 在 `backend/models/job.py` `JobInstance` 类中添加对应 ORM Column
3. 重写 `backend/services/post_completion.py`：
   - `run_post_completion()` 中将 `db.get(TaskRun, run_id)` 改为 `db.get(JobInstance, run_id)`
   - 缓存写入 `job.report_json` / `job.jira_draft_json` / `job.post_processed_at`
   - 通知上下文从 `Task` 改为 `WorkflowDefinition` + `TaskTemplate`
4. 在 recycler `_mark_timeout()` 中恢复 `run_post_completion_async(job.id)` 调用
5. 在 `agent_api.py` `complete_job()` 中添加 `run_post_completion_async(job_id)` 调用
6. 更新 `tasks.py` cached-report 端点（`/report/cached`, `/jira-draft/cached`）改为优先查 `JobInstance`
7. 新端点（orchestration.py 的 3 个 stub）实现完整报告逻辑，移除 501 状态

## 关联实现/文档

- `backend/main.py` — 已移除运行时 DDL
- `backend/alembic/env.py`
- `backend/alembic/versions/` — 当前 7 个迁移文件：
  - `001_add_device_monitoring.py`
  - `a1b2c3d4e5f6_add_stp_spec_phase1_schema.py` — **FROZEN，不可执行**
  - `b0f805bf6cee_add_users_table.py`
  - `c1a2b3d4e5f6_add_run_steps_and_pipeline_def.py`
  - `d1e2f3a4b5c6_add_monitoring_fields.py`
  - `e2f3a4b5c6d7_add_workflow_fields_to_task_schedules.py`
  - `f4a5b6c7d8e9_add_action_template_table.py`
- `backend/models/schemas.py` — 旧模型（双轨合并后清理）
- `backend/models/host.py` / `job.py` / `workflow.py` / `tool.py` — 新模型
- `docs/production-minimum-deployment-checklist.md`
