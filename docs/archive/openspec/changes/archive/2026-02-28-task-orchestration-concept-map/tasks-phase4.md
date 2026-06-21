# Phase 4 实施计划：前端重构 — 编排与执行核心页面

**Change**: task-orchestration-concept-map / Phase 4
**Date**: 2026-02-28
**Status**: Complete
**实施完成时间**: 2026-02-28
**前置条件**: Phase 1 (数据模型) + Phase 2 (Agent MQ) + Phase 3 (后端 API) 完成

---

## 总览

```
Phase A: Foundation
  Task 1: api.ts — 新类型定义（WorkflowDefinition / WorkflowRun / JobInstance / StepTrace / ToolEntry）
  Task 2: api.ts — 新 API 命名空间（api.orchestration.* / api.execution.* / api.toolCatalog.*）

Phase B: Orchestration 页面
  Task 3: WorkflowDefinitionListPage   /orchestration/workflows   [前端]
  Task 4: WorkflowDefinitionEditPage   /orchestration/workflows/:id [前端]

Phase C: Execution 页面
  Task 5: DispatchEntryPage             /execution/run              [前端]
  Task 6: WorkflowRunMatrixPage         /execution/runs/:run_id     [前端]

Phase D: 导航集成
  Task 7: Router 更新 — 注册新路由
  Task 8: Sidebar 更新 — 新导航分组
```

依赖：Task 1 → Task 2 → Task 3+4+5（并行）→ Task 6 → Task 7+8（并行）

---

## Phase A: Foundation

### Task 1: api.ts 新类型定义

**文件**: `frontend/src/utils/api.ts`（追加）

#### 1.1 新 Tool 模型（Phase 3 后端 tool_catalog.py 输出格式）

```typescript
export interface ToolEntry {
  id: number;
  name: string;
  version: string;
  script_path: string;
  script_class?: string | null;
  param_schema: Record<string, any>;
  is_active: boolean;
  created_at: string;
}
```

#### 1.2 新 Pipeline 格式（stages 对象，替代旧 phases 数组）

```typescript
export interface PipelineStep {
  step_id: string;
  action: string;            // "tool:<id>" | "builtin:<name>"
  version?: string;          // tool: action 时必填
  params?: Record<string, any>;
  timeout_seconds: number;
  retry?: number;
}

export interface PipelineDef {
  stages: {
    prepare?: PipelineStep[];
    execute?: PipelineStep[];
    post_process?: PipelineStep[];
  };
}
```

#### 1.3 WorkflowDefinition（声明式蓝图）

```typescript
export interface TaskTemplateEntry {
  id: number;
  workflow_definition_id: number;
  name: string;
  sort_order: number;
  pipeline_def: PipelineDef;
}

export interface WorkflowDefinition {
  id: number;
  name: string;
  description?: string | null;
  failure_threshold: number;           // 0.0 ~ 1.0
  task_templates?: TaskTemplateEntry[];
  created_at: string;
}

export interface WorkflowDefinitionCreate {
  name: string;
  description?: string;
  failure_threshold?: number;
  task_templates?: Omit<TaskTemplateEntry, 'id' | 'workflow_definition_id'>[];
}
```

#### 1.4 WorkflowRun + JobInstance + StepTrace（执行层）

```typescript
export type WorkflowStatus = 'RUNNING' | 'SUCCESS' | 'PARTIAL_SUCCESS' | 'FAILED' | 'DEGRADED';
export type JobStatus = 'PENDING' | 'RUNNING' | 'COMPLETED' | 'FAILED' | 'ABORTED' | 'UNKNOWN' | 'PENDING_TOOL';

export interface StepTrace {
  id: number;
  job_id: number;
  step_id: string;
  stage: 'prepare' | 'execute' | 'post_process';
  event_type: 'STARTED' | 'COMPLETED' | 'FAILED' | 'RETRIED';
  status: string;
  output?: string | null;
  error_message?: string | null;
  original_ts: string;
}

export interface JobInstance {
  id: number;
  workflow_run_id: number;
  task_template_id: number;
  host_id: string;
  device_id: number;
  status: JobStatus;
  status_reason?: string | null;
  created_at: string;
  updated_at: string;
  step_traces?: StepTrace[];
}

export interface WorkflowRun {
  id: number;
  workflow_definition_id: number;
  status: WorkflowStatus;
  failure_threshold: number;
  triggered_by?: string | null;
  started_at: string;
  ended_at?: string | null;
  jobs?: JobInstance[];
}

export interface WorkflowRunCreate {
  device_ids: number[];
  failure_threshold?: number;
}
```

---

### Task 2: api.ts 新 API 命名空间

#### 2.1 ApiResponse 解包工具函数

```typescript
// 解包后端统一响应格式 { data: T, error: null }
async function unwrapApiResponse<T>(request: Promise<any>): Promise<T> {
  const resp = await request;
  if (resp.data?.error) throw new Error(`[${resp.data.error.code}] ${resp.data.error.message}`);
  return resp.data?.data ?? resp.data;
}
```

#### 2.2 api.orchestration 命名空间

```
GET  /api/v1/workflows         → list(skip, limit)
POST /api/v1/workflows         → create(data)
GET  /api/v1/workflows/:id     → get(id)
PUT  /api/v1/workflows/:id     → update(id, data)
DEL  /api/v1/workflows/:id     → delete(id)
POST /api/v1/workflows/:id/run → run(id, data) → WorkflowRun
```

#### 2.3 api.execution 命名空间

```
GET /api/v1/workflow-runs/:run_id       → getRun(runId) → WorkflowRun
GET /api/v1/workflow-runs/:run_id/jobs  → getRunJobs(runId) → JobInstance[]
```

#### 2.4 api.toolCatalog 命名空间

```
GET    /api/v1/tools      → list(isActive?)  → ToolEntry[]
GET    /api/v1/tools/:id  → get(id)          → ToolEntry
POST   /api/v1/tools      → create(data)     → ToolEntry
PUT    /api/v1/tools/:id  → update(id, data) → ToolEntry
DELETE /api/v1/tools/:id  → remove(id)       → void
```

---

## Phase B: Orchestration 页面

### Task 3: WorkflowDefinitionListPage

**文件**: `frontend/src/pages/orchestration/WorkflowDefinitionListPage.tsx`（新建）

```
职责：
  - 列出所有 WorkflowDefinition（分页）
  - 每行展示：名称、描述（截断）、失败阈值、任务模板数量、创建时间
  - 操作：新建（模态框）、编辑（→ /orchestration/workflows/:id）、删除（确认对话框）
  - 右上角"发起测试"按钮 → /execution/run

UI 规格：
  - 与现有页面风格一致（Card + Table 布局，无 external UI 依赖）
  - 支持加载骨架屏
  - 新建表单字段：name (必填), description (可选), failure_threshold (0.00~1.00, 默认 0.05)
```

### Task 4: WorkflowDefinitionEditPage

**文件**: `frontend/src/pages/orchestration/WorkflowDefinitionEditPage.tsx`（新建）

```
职责：
  - 展示 WorkflowDefinition 详情 + 编辑基本信息
  - 内嵌 PipelineEditor（三列布局：prepare | execute | post_process）
  - PipelineEditor Step 卡片的 action 从 Tool Catalog 下拉选择
  - 保存时调用 PUT /api/v1/workflows/:id

依赖：
  - 复用现有 PipelineEditor.tsx（可扩展 stages 模式支持）
  - 复用 actionCatalog.ts（新增 tool: action 类型）

注意事项（Phase 4 范围内）：
  - PipelineEditor 的完整 stages 格式支持为本任务关键内容
  - 如现有 PipelineEditor 不支持三列 stages 模式，创建 StagesPipelineEditor.tsx
```

---

## Phase C: Execution 页面

### Task 5: DispatchEntryPage

**文件**: `frontend/src/pages/execution/DispatchEntryPage.tsx`（新建）

```
职责：
  1. 下拉选择 WorkflowDefinition（含描述预览）
  2. 多选设备（列表 + 过滤，显示 serial/status/host）
  3. 失败阈值（数字输入，默认来自 WorkflowDefinition.failure_threshold）
  4. 提交 → POST /api/v1/workflows/:id/run
  5. 成功后跳转 /execution/runs/:run_id

约束：
  - 提交按钮在请求期间 disabled（防重复提交）
  - 未选设备时禁止提交
```

### Task 6: WorkflowRunMatrixPage

**文件**: `frontend/src/pages/execution/WorkflowRunMatrixPage.tsx`（新建）

```
职责：
  - 展示 WorkflowRun 元信息（蓝图名、状态徽章、进度、开始时间）
  - 设备矩阵（每个方块 = 一个 JobInstance）
  - 方块颜色编码：
      PENDING      → 灰
      RUNNING      → 蓝（脉冲）
      COMPLETED    → 绿
      FAILED       → 红
      ABORTED      → 橙
      UNKNOWN      → 黄（警告条纹）
      PENDING_TOOL → 紫
  - 点击方块 → 展开侧边详情（StepTrace 时间线 + 状态原因）
  - 底部聚合统计

WebSocket（Phase 4 内实现，降级方案：30s 轮询）:
  ws://server/ws/workflow-runs/:run_id → 订阅状态更新
  消息处理：
    job_status     → 更新对应 JobInstance 方块颜色
    workflow_status → 终态时关闭连接
```

---

## Phase D: 导航集成

### Task 7: Router 更新

**文件**: `frontend/src/router/index.tsx`（修改）

新增路由：
```
/orchestration/workflows        → WorkflowDefinitionListPage (lazy)
/orchestration/workflows/:id    → WorkflowDefinitionEditPage (lazy)
/execution/run                  → DispatchEntryPage (lazy)
/execution/runs/:run_id         → WorkflowRunMatrixPage (lazy)
```

### Task 8: Sidebar 更新

**文件**: `frontend/src/layouts/Sidebar.tsx`（修改）

更新"任务编排"分组（路径调整到 /orchestration/* 前缀）：
```
任务编排:
  /orchestration/workflows  工作流设计   Workflow icon
  /orchestration/workflows  任务模板     FileBox icon (指向 WorkflowDefinition 内部管理)
  /schedules                定时任务     Clock icon

执行中心:
  /execution/run            发起测试     Rocket icon (新增)
  /execution/runs → /task-runs  任务实例  ListTodo icon
  /logs                     日志监控     FileSearch icon
```

---

## 验收标准

- [x] `tsc --noEmit` 无新增类型错误（Phase 4 代码无 TS 错误）
- [x] `/orchestration/workflows` 可正确列出 WorkflowDefinition 并支持创建
- [x] `/orchestration/workflows/:id` 展示 Pipeline 编辑器，保存时同步 task_templates
- [x] `/execution/run` 可选择蓝图+设备并发起调度，成功后跳转矩阵页
- [x] `/execution/runs/:run_id` 展示 Job 方块矩阵，颜色与状态对应，WebSocket 实时推送
- [x] Sidebar 导航正确跳转，高亮激活项
- [x] `/workflows` 重定向到 `/orchestration/workflows`（修复 Legacy WorkflowsPage 回归）
- [x] 后端 `PUT /api/v1/workflows/{id}` 新增 `task_templates` 字段支持

## 遗留项（后续优化）

- [x] 矩阵方块显示设备 serial（后端 JobInstanceOut 增加 device_serial + 批量 JOIN Device，前端 JobBlock/JobDrawer 展示）
- [x] Job Drawer 实时日志面板（ws/jobs/{job_id}/logs，前端 JobLogStream 组件，Tab 切换）
- [x] 缺失路由：`/execution/runs` 历史列表（WorkflowRunListPage + GET /workflow-runs + api.execution.listRuns）
- 设备筛选支持 platform/tags（当前仅 serial/model 文本过滤）
- StagesPipelineEditor params 字段按 param_schema 校验
