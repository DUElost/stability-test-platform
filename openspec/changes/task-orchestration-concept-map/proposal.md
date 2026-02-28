# OPSX Proposal: 任务编排概念图谱 — 三层关系研究

**Change ID**: task-orchestration-concept-map
**Date**: 2026-02-26
**Status**: Research Complete — Decisions Recorded
**Type**: Architecture Clarification (Non-Implementation)

---

## Context

当前代码库中存在三个彼此相关但边界模糊的领域：

1. **任务编排 / 工作流设计** — Workflow（DB 实体）vs Pipeline（Task 上的 JSON blob）
2. **任务模板 vs 执行实例** — Task / TaskTemplate / TaskRun / RunStep 的职责划分
3. **工具管理 / 专项管理** — Tool 目录、ToolCategory、`tool:<id>` action、`builtin:run_tool_script`

本研究通过全量代码探索，产出以下内容：
- 当前系统的**概念图谱**（边界清晰定义）
- **硬约束集合**（不可违反的技术事实）
- **软约束集合**（当前惯例，可演进）
- **待解歧义**（需用户确认后方可指导后续开发）

---

## 当前系统概念图谱

### 概念层级（从高到低）

```
[工作流层 Workflow Layer]
  Workflow (DB) ←→ WorkflowStep (DB)
        └── step.tool_id → Tool (DB)
        └── step.task_type → (创建 Task 的类型标记)
        └── step.task_run_id → TaskRun (执行后填充)

[任务层 Task Layer]
  Task (DB, "模板概念") ←→ pipeline_def (JSON on Task)
        └── task.tool_id = DISABLED (HTTP 400)
        └── task.template_id → TaskTemplate (旧版目录, 逐步废弃)
        └── 文件模板 pipeline_templates/*.json = PipelineEditor 的种子数据

  TaskRun (DB, "实例概念")
        └── run.task_id → Task
        └── RunStep[] = 从 Task.pipeline_def 展平 (延迟创建)

[执行层 Pipeline Execution Layer]
  PipelineEngine (Agent 侧)
        └── 阶段 (Phase): 串行执行
        └── 步骤 (Step): 串行 or 并行 (max 4 workers)
        └── action 解析: builtin:<name> | shell:<cmd> | tool:<id>(DISABLED)
        └── builtin:run_tool_script → 动态加载 Tool.script_path / script_class

[工具目录层 Tool Catalog Layer]
  ToolCategory (DB)
        └── Tool (DB) — script_path, script_class, param_schema, default_params
        └── 通过 GET /api/v1/tools 列出
        └── 通过 POST /api/v1/tools/scan 从文件系统同步
```

---

## 三大领域的边界定义

### A. 任务编排 / 工作流设计

**当前存在两种独立的编排机制，未整合：**

| 维度 | Workflow (DB 实体) | Pipeline (Task.pipeline_def JSON) |
|------|-------------------|-----------------------------------|
| 粒度 | 高层：多 Task 序列 | 低层：单 Task 内部的 Phase/Step |
| 持久化 | tasks → task_runs 的步进式状态机 | JSON blob on Task，只读描述 |
| 执行者 | 服务端调度器（workflows.py） | Agent PipelineEngine |
| 关联 Tool | WorkflowStep.tool_id (FK) | `builtin:run_tool_script` (间接) |
| 前端界面 | **未实现**（API 存在，无前端页面） | PipelineEditor（已实现） |
| 模板复用 | is_template flag + clone API | 文件系统模板 + Load Template UI |

**结论**：Workflow 和 Pipeline 是**平行的、未整合的**两套编排机制，分别针对不同抽象层次。

---

### B. 任务模板 vs 执行实例

**三种"模板"概念共存：**

| 名称 | 类型 | 作用 | 状态 |
|------|------|------|------|
| `TaskTemplate` (DB) | 旧版任务配置目录 | 存储 type + default_params | 逐步废弃，被 pipeline_def 取代 |
| `Task` (DB) | 可复用的任务定义 | 包含 pipeline_def + params，可多次 dispatch | **当前主力** |
| 文件模板 (`pipeline_templates/*.json`) | 文件系统 JSON | PipelineEditor 的初始化种子 | 只读参考，不存 DB |

**执行实例链：**

```
Task (定义)
  → dispatch → TaskRun (QUEUED, 实例)
  → agent poll → DISPATCHED → RUNNING
  → pipeline_def 展平 → RunStep[] (延迟创建，首次 RUNNING heartbeat)
  → PipelineEngine 执行每个 step
  → complete → FINISHED/FAILED
```

**关键约束**：
- `Task` 不复制 `pipeline_def` 到 `TaskRun`；`TaskRun` 通过 task_id FK 继承
- `RunStep` 在 dispatch 时**不存在**，前端在 dispatch 后短暂看不到步骤
- 分布式任务 (`is_distributed=True`) 在 Task 创建时自动为每台设备创建 TaskRun

---

### C. 工具管理 / 专项管理

**Tool 实体的三种使用路径：**

| 路径 | 机制 | 状态 |
|------|------|------|
| `pipeline_def` 中 `action: "tool:<id>"` | PipelineEngine 解析 tool_id → 执行 | **DISABLED**（永远返回失败） |
| `pipeline_def` 中 `action: "builtin:run_tool_script"` + params `{script_path, script_class}` | 动态 import Python 类 | **当前推荐路径** |
| `WorkflowStep.tool_id` | Workflow 层步骤直接关联 Tool | API 存在，执行逻辑待探索 |

**前端 ActionSelector 现状**：
- 仅暴露 `builtin:<name>` 和 `shell:` 两种 action 类型
- `tool:<id>` 不在下拉列表中
- Tool DB 记录**不会**动态加载到编辑器

---

## 硬约束集合 (Hard Constraints)

> 这些约束反映代码已实现的现实，不可在不修改代码的情况下绕过。

1. **`pipeline_def` 是 Task 创建的必填字段**（API 422 if absent, `tasks.py:423-424`）
2. **`task.tool_id` 和 `task.tool_snapshot` 被 API 拦截**（创建时传入返回 HTTP 400, `tasks.py:425-426`）
3. **`tool:<id>` action 类型在 PipelineEngine 中永远失败**（`pipeline_engine.py _resolve_action`）
4. **`pipeline_schema.json` 的 action 校验规则：`^(builtin:|shell:).+`**，不允许 `tool:` 前缀
5. **RunStep 延迟创建**：dispatch 后到首次 RUNNING heartbeat 之前，RunStep 不存在
6. **并行 Phase 最多 4 个并发 Step**（ThreadPoolExecutor max_workers 硬编码）
7. **Retry 延迟固定 5 秒**，无指数退避
8. **Workflow 只能在 DRAFT 状态删除**（非 DRAFT 返回 409）
9. **AuditLog 要求认证用户**，匿名请求不记录审计

---

## 软约束集合 (Soft Constraints / Current Conventions)

1. **文件模板命名约定**：`monkey.json`, `mtbf.json`, `ddr.json` 等按测试类型命名
2. **Phase 命名惯例**：`prepare` → `execute` → `post_process`/`teardown`
3. **builtin:run_tool_script 是 Tool 脚本进入 Pipeline 的唯一推荐途径**
4. **Task.type 字段仅用于展示/统计，不影响执行逻辑**（执行由 pipeline_def 驱动）
5. **工具目录使用 param_schema 驱动前端 DynamicToolForm 渲染**

---

## 依赖关系 (Cross-module Dependencies)

```
PipelineEditor (前端)
  → depends on: actionCatalog.ts (硬编码的 builtin actions)
  → depends on: GET /api/v1/pipeline/templates (文件模板)
  → does NOT depend on: GET /api/v1/tools (工具 DB 未桥接)

PipelineEngine (Agent)
  → depends on: Task.pipeline_def (via agent poll)
  → depends on: RunStep DB rows (via HTTP fallback or WebSocket)
  → depends on: builtin action registry (static, at import time)
  → does NOT depend on: Tool DB at runtime

Workflow 系统 (后端)
  → depends on: Tool DB (WorkflowStep.tool_id)
  → has NO frontend implementation
  → execution logic in workflow_executor.py (not explored)
```

---

## 待解歧义 (Open Questions for User)

以下问题需要用户确认，每个问题都对应一个重大开发决策方向：

### Q1: Workflow 与 Pipeline 是否计划整合？

- **现状**：两套独立编排机制，Workflow 侧重跨任务序列，Pipeline 侧重单任务内部步骤
- **影响**：如果整合，需要设计从 Workflow → pipeline_def 的映射；如果不整合，则需要明确各自的使用场景文档

### Q2: `tool:<id>` action 类型的未来？

- **现状**：已在 PipelineEngine 中禁用，替代品是 `builtin:run_tool_script`
- **影响**：
  - 若永久禁用 → 清理 schema 和引擎中的死代码，统一使用 `builtin:run_tool_script`
  - 若计划重新启用 → 需要在 pipeline_schema.json 中允许 `tool:` 前缀，PipelineEngine 需实现 DB 查询

### Q3: PipelineEditor 是否需要动态加载 Tool DB？

- **现状**：ActionSelector 只显示硬编码的 builtin actions，用户无法从 UI 选择已注册的 Tool
- **影响**：如果要支持，需要在 ActionSelector 中集成 GET /api/v1/tools，并解决 `tool:<id>` action 被禁用的矛盾

### Q4: 模板变量 `{placeholder}` 的插值时机？

- **现状**：文件模板中含有 `{log_dir}`, `{apk_path}` 等占位符，但 PipelineEngine 和 schema 均无插值逻辑
- **影响**：
  - 若在前端 PipelineEditor 中填充 → 需要模板参数表单
  - 若在 Agent 运行时插值 → 需要在 PipelineEngine 中实现变量解析

### Q5: `_db_step_id` 注入机制缺失问题

- **现状**：PipelineEngine 依赖 `step_def.get('_db_step_id', 0)` 上报步骤状态，但 pipeline_def 在传输时不含 step ID（RunStep 是延迟创建的）
- **实际效果**：所有步骤状态更新时 `step_id=0`，后端无法精确关联到 RunStep 行
- **影响**：步骤级别的状态跟踪（PipelineStepTree 前端视图）可能不准确

### Q6: Workflow 前端页面的优先级？

- **现状**：workflows.py API 完整（CRUD + start/cancel/clone/template toggle），但无前端页面
- **已有**：ADR-0013 中 ResourcesPage、IssueTrackerPage 等已新增，Workflow 页面未提及
- **影响**：是否在下一阶段开发 Workflow 管理 UI？

---

## 成功判据 (Success Criteria)

本研究的成功定义为：
1. ✅ 三个核心概念的边界已清晰定义（见上方概念图谱）
2. ✅ 硬约束已识别并记录（9条，不可绕过）
3. ✅ 待解歧义已梳理（6条，需用户确认）
4. ⬜ 用户对 6 个歧义问题给出明确答案
5. ⬜ 产出的约束集可直接用于下一阶段的 spec-plan 或开发任务

---

## 用户决策记录 (2026-02-26)

| 歧义问题 | 用户决策 | 新增约束 |
|---------|---------|---------|
| Workflow 与 Pipeline 是否整合？ | **计划整合** | Workflow 的执行层未来也将由 pipeline_def 驱动；整合方案需在下阶段 spec-plan 中设计 |
| `tool:<id>` action 未来方向？ | ~~永久禁用，清理死代码~~ → **2026-02-27 撤销：采用 stp-spec，重新启用 tool:<id>** | 实现 ToolRegistry 版本管理；pipeline_def 中的 tool:<id> 成为主要机制 |
| 模板占位符解析位置？ | **前端填充** | PipelineEditor 在 Load Template 后需检测 `{placeholder}` 并显示参数输入表单，用户填写后替换占位符再提交 |
| Workflow 前端页面优先级？ | **高优先级，下阶段实现** | Workflow 管理页面（列表/创建/启动/取消/克隆/标记模板）为下一开发阶段的目标 |

---

## 待开发任务（约束驱动）

基于本次研究和用户决策，识别出以下具体开发任务：

### 任务 1：清理 `tool:<id>` 死代码 [小型，低风险]
- `backend/schemas/pipeline_schema.json`：action pattern 仅保留 `^(builtin:|shell:).+`
- `backend/agent/pipeline_engine.py`：删除 `tool:` 分支逻辑
- `frontend/src/components/pipeline/PipelineEditor.tsx`：validatePipeline regex 已符合（无需改动）

### 任务 2：PipelineEditor 模板参数填充 UI [中型]
- Load Template 后解析占位符 `{param_name}` → 弹出参数输入对话框
- 用户填写后替换所有占位符，再渲染到 PipelineEditor
- 涉及文件：`PipelineEditor.tsx`, `CreateTask.tsx`

### 任务 3：Workflow 前端管理页面 [大型]
- 路由：`/workflows` 列表页 + `/workflows/new` 创建页
- 功能：CRUD + Start/Cancel/Clone/标记模板
- 依赖 API：`GET/POST/DELETE /api/v1/workflows`, `POST /api/v1/workflows/{id}/start`, 等
- 需探索 `workflow_executor.py` 执行逻辑（未在本次研究中覆盖）

### 任务 4：Workflow + Pipeline 整合架构设计 [架构级，需先 spec-plan]
- 目标：Workflow 的每个 WorkflowStep 最终创建一个带 pipeline_def 的 Task 并执行
- 需澄清：WorkflowStep 中的 task_type + tool_id 如何映射为 pipeline_def
- 建议先做任务 3（前端页面），再做整合架构

### 任务 5：修复 `_db_step_id` 注入机制 [中型，影响步骤状态跟踪精度]
- **现状**：Agent PipelineEngine 用 `step_def.get('_db_step_id', 0)` 上报步骤状态，但 RunStep 是延迟创建的，pipeline_def 中无 step ID，导致所有步骤状态更新 step_id=0
- **方案**：Agent 在首次 RUNNING heartbeat 后，调用 `GET /api/v1/runs/{run_id}/steps` 获取 RunStep 列表，按 (phase, step_order) 匹配后注入 `_db_step_id`
- 涉及文件：`backend/agent/pipeline_engine.py`, `backend/agent/main.py` 或 `task_executor.py`

---

## 附录：关键文件路径速查

| 领域 | 文件 | 说明 |
|------|------|------|
| 数据模型 | `backend/models/schemas.py` | Task, TaskRun, RunStep, Tool, ToolCategory, Workflow, WorkflowStep |
| API 模型 | `backend/api/schemas.py` | Pydantic 验证模型 |
| Task API | `backend/api/routes/tasks.py` | 创建/分发，pipeline_def 验证，RunStep 延迟创建 |
| Tool API | `backend/api/routes/tools.py` | CRUD + scan + bootstrap |
| Workflow API | `backend/api/routes/workflows.py` | 完整 CRUD，无前端 |
| Pipeline 引擎 | `backend/agent/pipeline_engine.py` | 执行引擎，action 解析，禁用 tool:<id> |
| Pipeline Schema | `backend/schemas/pipeline_schema.json` | JSON Schema 验证，action pattern |
| 文件模板 | `backend/schemas/pipeline_templates/` | monkey.json, mtbf.json, ddr.json, gpu.json 等 |
| 前端 Action 目录 | `frontend/src/components/pipeline/actionCatalog.ts` | 硬编码的 builtin actions，无 Tool DB 集成 |
| 前端编辑器 | `frontend/src/components/pipeline/PipelineEditor.tsx` | 可视化编辑器 |
| ADR 参考 | `docs/adr/ADR-0007-*.md` | Tool+Template+Workflow 三层模型决策 |
| ADR 参考 | `docs/adr/ADR-0014-*.md` | Pipeline 执行引擎架构 |
