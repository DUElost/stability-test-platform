---
name: 双轨合并路线图 v3 修订版
overview: 针对 Tool 迁移覆盖面、Dispatcher 路径、API 收口策略及报告语义进行深度优化的终版路线图。采用“后端先行 + 兼容壳”策略，分 6 个波次渐进式完成。
todos:
  - id: wave0-cleanup
    content: "Wave 0-1: 删除死代码 workflows.py + 清理 schemas.py 中对应的 Pydantic schema"
    status: done
  - id: wave0-enums
    content: "Wave 0-2: 统一枚举到 enums.py，schemas.py 改为 re-export"
    status: done
  - id: wave0-models
    content: "Wave 0-3: 补齐 JobArtifact 模型，保持 AuditLog 表名为 audit_logs"
    status: done
  - id: wave0-relationships
    content: "Wave 0-4: 为 job.py/workflow.py 补充 ORM relationship"
    status: done
  - id: wave1-tool
    content: "Wave 1: Tool API 收口至 tool_catalog.py + 补齐 Tool.category + 前后端深度适配"
    status: done
  - id: wave2-audit
    content: "Wave 2: AuditLog 迁移（保留 audit_logs 表名）"
    status: done
  - id: wave3a-services
    content: "Wave 3a: 后端服务层迁移 — recycler + report_service + post_completion"
    status: done
  - id: wave3a-post-completion
    content: "Wave 3a-补充: post_completion.py 迁移（依赖 job_instance 表新增 report_json/jira_draft_json/post_processed_at 列）"
    status: done
  - id: wave3b-api
    content: "Wave 3b: API 端点迁移 + 区分 Single-Job Report 与 Workflow Aggregate Report"
    status: done
  - id: wave3c-frontend
    content: "Wave 3c: 前端 api.ts 新增 execution report/jira/summary 方法"
    status: done
  - id: wave3d-cleanup
    content: "Wave 3d: 清理 schemas.py — User/Notification/Schedule 分离到独立模块；conftest 新模型 fixtures"
    status: done
  - id: wave3e-tests
    content: "Wave 3e: 测试迁移（conftest/pbt/concurrent + 前端测试）— 新 fixtures 已添加，旧测试保留"
    status: done
  - id: wave4-migration
    content: "Wave 4: 重写 Phase 1 为 3 步渐进式迁移脚本（create -> columns -> data）"
    status: done
  - id: wave5-ci
    content: "Wave 5: CI 一致性检查（migration chain lint + env.py import check + legacy import guard）"
    status: done
  - id: wave6-drop
    content: "Wave 6: schemas.py → legacy.py 迁移 + DROP migration i7d8e9f0a1b2 创建"
    status: done
isProject: false
---

# 双轨合并全量路线图（ADR-0008 落地 - v3 修订版）

## 现状概览与纠偏

- Dispatcher 归口：旧 `scheduler/dispatcher.py` 已废弃，核心逻辑位于 `backend/services/dispatcher.py`。
- Tool API 现状：旧 `api/routes/tools.py` 未挂载，当前在线的是 `api/routes/tool_catalog.py`（前缀 `/api/v1/tools`）。
- 概念映射精细化：
  - 旧 `Task` -> 新 `WorkflowDefinition` (WD)。
  - 旧 `TaskRun` -> 新 `JobInstance` (JI)；兼容 API 中的 `run_id` 语义实际映射 `JI.id`。
  - 旧 `RunStep` -> 新 `StepTrace` (ST)。
- 报告语义区分：
  - Single-Job Report：对应兼容层 `/runs/{run_id}/report`，以单个 `JobInstance` 为中心。
  - Workflow Summary：新端点 `/workflow-runs/{id}/summary`，提供全运行生命周期的聚合视图。

---

## Wave 0: 清理与补齐（前置准备）

### 0-1. 删除死代码 workflows.py

确认 `backend/api/routes/workflows.py` 未在 `main.py` 挂载，直接删除。同步清理 `backend/api/schemas.py` 中对应的 `WorkflowCreate`、`WorkflowOut`、`WorkflowStepOut` schema。

### 0-2. 统一枚举定义

以 `backend/models/enums.py` 为唯一枚举源，`schemas.py` 仅保留 re-export。

### 0-3. 补齐新模型缺失

- `JobArtifact`：加入 `backend/models/job.py`，替代旧 `LogArtifact`。
- `AuditLog`：新建 `backend/models/audit.py`，保持 `__tablename__ = "audit_logs"`，规避 rename 风险。
- `Tool.category`：在 `backend/models/tool.py` 中添加 `category` 字段，用于平替旧 `ToolCategory`。

### 0-4. 补齐 ORM Relationship

为 `backend/models/job.py` 和 `backend/models/workflow.py` 中的 FK 补全 `relationship()`，确保联查与级联能力。

---

## Wave 1: Tool 迁移（深度收口）

目标：彻底废弃旧 `ToolCategory` 模型，将分类逻辑平移至新 `Tool.category` 字段，并完成全链路适配。

### 1-1. 后端 API 收口

- 弃用 `backend/api/routes/tools.py`：确认不恢复挂载旧 `tools.py`。
- 升级 `backend/api/routes/tool_catalog.py`：
  - `ToolOut` 补全 `category` 字段。
  - 新增 `GET /tools/categories` 端点，通过 `SELECT DISTINCT category FROM tool` 返回派生分类列表。
  - 新增 `GET /tools?category={name}` 过滤逻辑。

### 1-2. 后端依赖面适配

- `backend/core/tool_bootstrap.py`：从 `ToolCategory/category_id` 改为直接维护 `Tool.category`。
- `backend/agent/tool_discovery.py`：发现结果直接写入 `Tool.category`，移除对 `ToolCategory` 的查表与建表逻辑。
- `backend/agent/tests/test_tool_bootstrap.py`：改为断言 `Tool.category`，不再断言 `ToolCategory` 行存在。

### 1-3. 前端全量适配

- `frontend/src/utils/api.ts`：更新 `Tool` 类型定义，将 `category_id/category_name` 改为 `category: string`；更新 `api.tools.*` 方法群。
- `frontend/src/pages/tools/ToolsPage.tsx`：分类管理改为基于字符串 `category` 的派生列表，不再保留分类 CRUD 资源视图。
- `frontend/src/components/task/ToolSelector.tsx`：适配新的字符串分类过滤逻辑。
- `frontend/src/components/pipeline/actionCatalog.ts`：适配分类展示。

### 1-4. 数据迁移规则

- 旧 `tools.category_id -> tool.category`：通过关联旧 `tool_categories.name` 回填字符串分类。
- 旧 `tools.enabled -> tool.is_active`。
- 旧 `tools.default_params`、`script_type`、`timeout`、`need_device` 若新模型无直接承载字段，需在 Wave 1 先明确是补字段、并入 `param_schema`/`description`，还是确认废弃。
- 旧 `tools.version` 不存在时，需要定义默认值策略。
  建议：迁移脚本统一填充 `version = 'legacy'`，并在后续人工维护中逐步替换。

Alembic 脚本：将旧 `tool_categories` 数据迁移到 `tool.category` 后再 DROP `tool_categories`。

---

## Wave 2: AuditLog 迁移（低风险）

迁移 `audit.py`（routes/core）引用到新模型。保持表名 `audit_logs` 不变。

---

## Wave 3: Task/Run 核心迁移

### 3a. 后端服务层迁移

收口分发器：

- 核心任务：将所有分发调用点从 `scheduler/dispatcher.py` 迁移至 `services/dispatcher.py`。
- `backend/scheduler/cron_scheduler.py`：调用 `services/dispatcher.py`。
- `backend/services/report_service.py`：适配 `JobInstance` 报告构建。
- `backend/services/post_completion.py`：完成后处理适配。

### 3b. API 端点语义校准

单实例报告（Job Report）：

- 保留路由 `GET /runs/{run_id}/report`，内部逻辑映射到 `JobInstance.id`。
- 语义定义：侧重单个设备/任务执行细节。

聚合报告（Workflow Summary）：

- 新增路由 `GET /workflow-runs/{id}/summary` 位于 `orchestration.py`。
- 语义定义：侧重整个流水线的状态聚合、失败分布、总体覆盖率。

### 3c. 前端分步平滑迁移

- 第一步：`api.ts` 新增 `WorkflowRun` 类型，`api.tasks.getRunReport` 临时保持旧路径。
- 第二步：详情页、列表页逐个切回 `api.workflows.*` 调用。

### 3d. 清理与测试迁移

- 清理 `schemas.py` 冗余模型。
- `conftest.py` 工厂方法更新至新模型。

---

## Wave 4: 数据迁移（增量策略）

已将 Phase 1 冻结迁移 (`a1b2c3d4e5f6`) 重写为 CREATE-only 安全迁移（不删除遗留表），
并新增两个增量迁移：

- `g5b6c7d8e9f0`：添加 `job_instance` 后处理列 + `job_artifact` 表 + `tool.category` 字段
- `h6c7d8e9f0a1`：遗留表数据复制（hosts→host, devices→device, tools→tool, log_artifacts→job_artifact）

所有迁移均为幂等操作，可安全重复运行。遗留表将在 Wave 6 中删除。

---

## Wave 5: CI 一致性检查

CI 脚本 `scripts/ci_check_migrations.py` 执行三项检查：
1. 迁移链线性验证（单 head，无分支）
2. `alembic/env.py` 导入完整性（所有新模型模块）
3. 新模型模块禁止直接导入 `backend.models.schemas`

---

## Wave 6: 遗留表清除（已完成代码层面，DROP 待执行）

### 6-1. schemas.py → legacy.py

- 所有遗留模型（`Task`, `TaskRun`, `RunStep`, `LogArtifact`, `LegacyTaskTemplate`,
  `LegacyTool`, `LegacyToolCategory`）从 `schemas.py` 移入 `backend/models/legacy.py`。
- `schemas.py` 已删除；所有消费方（仅 `tasks.py`）改为从 `legacy.py` 导入。
- `alembic/env.py` 新增 `import backend.models.legacy`。
- `ci_check_migrations.py` 的 `NEW_MODEL_FILES` 补入 `legacy`。

### 6-2. DROP migration (i7d8e9f0a1b2)

迁移 `i7d8e9f0a1b2_drop_legacy_tables.py` 已创建，DROP 以下表（按依赖顺序）：

- `run_steps`, `log_artifacts`, `task_runs`, `tasks`, `task_templates`
- `tools`, `tool_categories`

**执行前提**：
1. 运行 `h6c7d8e9f0a1` 数据迁移确认无遗漏
2. 无 API 流量命中遗留端点
3. 已创建数据库备份

> `hosts`/`devices` 遗留 int-PK 表暂不在此迁移中 DROP，因新 `device` 表仍有
> `ForeignKey("devices.id")` 的间接引用（通过 `legacy.py` 中的 `TaskRun.device_id`）。
> 这些表将在 `legacy.py` 中的模型全部下线后单独处理。

---

## 实施关键决议

1. Tool 管理全部收口至 `tool_catalog.py`，前端同步切换到新工具 API，不再保留分类 CRUD 资源。
2. Dispatcher 统一到 `backend/services/dispatcher.py`，清理 `backend/scheduler/dispatcher.py` 的生产引用。
3. 文档、接口命名与注释中严禁混用 "Run Report" 和 "Workflow Summary"。
