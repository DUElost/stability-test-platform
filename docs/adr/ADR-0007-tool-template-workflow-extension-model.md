# ADR-0007: 工具配置 + 任务模板 + 工作流扩展模型
- 状态：Accepted（部分废弃，见下方变更记录）
- 日期：2026-02-18（2026-05-05 更新）
- 决策者：平台研发组
- 标签：可扩展性, 工具模型, 工作流, 定时任务

## 背景

稳定性专项（Monkey/MTBF/DDR/GPU/待机等）持续演进，如果每次都改核心调度代码会导致高耦合和交付变慢。

## 决策

### 当前架构（自 2026-05-04 起）

原三层扩展模型（Tool → Template → Workflow）已收敛为两层架构：

| 层级 | 数据实体 | 路由 | 说明 |
|------|---------|------|------|
| **脚本** | `Script`（name + version + nfs_path + default_params） | `/api/v1/scripts/*` | 唯一执行能力来源；action 类型统一为 `script:<name>` |
| **模板** | `ActionTemplate`（action 格式强制 `script:<name>`） | `/api/v1/action-templates` | 预定义参数集 + 脚本绑定 |
| **编排** | `Plan` + `PlanStep` → `PlanRun` → `JobInstance` | `/api/v1/plans/*`, `/api/v1/plan-runs/*` | 替代旧 Workflow（见 [ADR-0020](./ADR-0020-plan-step-one-shot-migration.md)） |
| **调度** | `TaskSchedule`（plan_id FK） | `/api/v1/schedules` | Cron 触发 Plan 执行 |

- Tool 层（ToolCategory + Tool + `/api/v1/tools`）已由 Script 目录机制替代：`backend/services/script_catalog.py` 扫描 + `backend/agent/registry/script_registry.py` 运行时解析
- Workflow 层（WorkflowDefinition + WorkflowRun + `dispatch_workflow`）已由 Plan 体系替代（见 ADR-0020）
- 核心调度仅消费统一 JobInstance 实体，不直接绑定具体专项实现细节

### 2026-05-04 收敛决策（supersedes 原三层模型）

1. Tool/ToolCategory ORM + `tool_catalog` 路由 + `/api/v1/tools` 端点 + `host.tool_catalog_version` 列全部删除（Alembic `5790a8de0a87` DROP）
2. `task_templates.py` 内置模板机制删除，由 `ActionTemplate` ORM + `/api/v1/action-templates` 替代
3. `services/dispatcher.py`（`dispatch_workflow`）删除，由 `plan_dispatcher*.py` 替代
4. 引擎唯一合法 action 类型 = `script:<name>`，唯一合法格式 = `lifecycle`（见 [ADR-0014](./ADR-0014-pipeline-execution-engine.md)）
5. Legacy 路由 `tools.py`、`workflows.py`、`orchestration.py`、`tool_catalog.py` 均已删除

## 备选方案与权衡

- 方案 A：每个专项写独立路由和独立调度流程。
  - 优点：短期开发快。
  - 缺点：长期形成脚本烟囱，复用差。
- 方案 B：当前方案（配置驱动 + 统一编排）。
  - 优点：扩展成本低，UI 与 API 一致性更好。
  - 缺点：配置治理与参数校验要求更高。

## 影响

- 正向影响：新增专项可通过脚本目录 + Plan 编排快速接入，版本化 + sha256 审计
- 代价：脚本参数契约需版本化（已存在版本的 default_params 不可修改，须新建版本）

## 落地与后续动作

- ✅ 已落地：脚本目录扫描 + Script CRUD + Plan 编排 + Cron 调度
- ✅ ActionTemplate 端点（action 格式强制 `script:<name>`）
- ~~✅ 已落地：工具 CRUD、扫描同步~~ → 已删除，由 Script 目录替代
- ~~✅ Phase 3 路由替代~~ → `orchestration.py`、`tool_catalog.py` 亦已删除
- ~~✅ Workflow 执行器重构~~ → `services/dispatcher.py` 亦已删除，由 Plan 体系替代
- ~~后续：建立"脚本版本 + 参数 Schema 校验 + 灰度发布"机制~~ → `param_schema` 运行时校验需求迁移至 [ADR-0023](./ADR-0023-script-traceability.md) C2-C8 实施前置项；"灰度发布"当前不合平台优先级，已移除

## 关联实现/文档

### 当前活跃
- `backend/models/script.py` — Script ORM 模型
- `backend/models/action_template.py` — ActionTemplate ORM 模型
- `backend/services/script_catalog.py` — 脚本目录扫描（替代 tool_catalog）
- `backend/agent/registry/script_registry.py` — ScriptRegistry（运行时解析 `script:<name>`）
- `backend/api/routes/scripts.py` — Script 管理 API（替代 tools / tool_catalog）
- `backend/api/routes/action_templates.py` — Action 模板端点
- `backend/api/routes/plans.py` — Plan CRUD + 触发执行
- `backend/api/routes/plan_runs.py` — PlanRun 查询 + 聚合
- `backend/api/routes/schedules.py` — 定时任务
- `backend/services/plan_dispatcher.py` / `plan_dispatcher_core.py` / `plan_dispatcher_sync.py` — Plan 派发（替代 dispatcher.py）
- `backend/scheduler/cron_scheduler.py` — Cron 调度器

### 关联 ADR
- [ADR-0014](./ADR-0014-pipeline-execution-engine.md) — Pipeline 执行引擎（lifecycle + script 单轨）
- [ADR-0020](./ADR-0020-plan-step-one-shot-migration.md) — Plan-based 编排替代 Workflow
- [ADR-0023](./ADR-0023-script-traceability.md) — 脚本可溯源性

---

## 历史决策（已废弃）

<details>
<summary>原三层扩展模型（2026-02 至 2026-05-04）</summary>

### 原决策

采用"三层扩展"模型：

- 工具层：`ToolCategory` + `Tool` 数据化配置，支持脚本路径、参数 Schema、超时等能力。
- 模板层：内置 `task_templates` 提供默认参数与脚本入口。
- 编排层：
  - Workflow 多步骤编排（顺序推进）。
  - Schedule（cron）定时创建任务。

### 原关联实现

#### 当前活跃
- `backend/models/tool.py` — Tool ORM 模型（含 `category` 字段，替代旧 `ToolCategory`）→ **已删除**
- `backend/core/task_templates.py` — 内置任务模板 → **已删除**
- `backend/api/routes/orchestration.py` — Workflow 编排端点 → **已删除**
- `backend/api/routes/tool_catalog.py` — 工具目录端点 → **已删除**
- `backend/api/routes/action_templates.py` — Action 模板端点 → **仍活跃**
- `backend/api/routes/schedules.py` — 定时任务 → **仍活跃**
- `backend/scheduler/cron_scheduler.py` — Cron 调度器 → **仍活跃**
- `backend/services/dispatcher.py` — Workflow 派发服务 → **已删除**

#### Legacy（保留但未挂载，待 Wave 8 移除）
- `backend/api/routes/tools.py` — 旧工具 CRUD 路由 → **已删除**
- `backend/api/routes/workflows.py` — 旧工作流 CRUD 路由 → **已删除**

</details>
