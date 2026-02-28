# 前端开发规范：页面结构、组件、WebSocket

## 1. 页面路由

```
/                            → 仪表盘 (Dashboard)
/orchestration
  /workflows                 → WorkflowDefinition 列表
  /workflows/:id             → 编辑蓝图（含 PipelineEditor）
  /tools                     → Tool Catalog 管理
  /templates                 → TaskTemplate 管理
/execution
  /run                       → 调度入口（Run Test）
  /runs                      → WorkflowRun 历史列表
  /runs/:run_id              → WorkflowRun 详情（工作流矩阵看板）
  /runs/:run_id/jobs/:job_id → 单设备 Job 详情（Step 时间线 + 实时日志）
/resources
  /hosts                     → Host 管理
  /devices                   → Device 管理
/settings                    → 系统设置
```

## 2. 核心页面规范

### 2.1 调度入口（/execution/run）

```
必填项：
  1. 选择 WorkflowDefinition（下拉，含描述预览）
  2. 选择设备（多选，支持按 platform/tags 筛选）
  3. 失败阈值（数字输入，默认 5%，取自 WorkflowDefinition.failure_threshold）

提交后：
  - 调用 POST /api/v1/workflows/{id}/run
  - 成功后立即跳转 /execution/runs/:run_id
  - 禁止重复提交（提交后按钮置 disabled，直到跳转完成）
```

### 2.2 WorkflowRun 矩阵看板（/execution/runs/:run_id）

**核心交互**：

```
顶部：WorkflowRun 元信息（名称、状态、进度条、开始时间）
      状态徽章：RUNNING(蓝) / SUCCESS(绿) / PARTIAL_SUCCESS(橙) / FAILED(红) / DEGRADED(灰)

主体：设备方块矩阵（每个方块 = 一个 JobInstance）
      方块内容：
        - 设备 serial（截断后 8 位）
        - 当前 Step 名称（或 COMPLETED / FAILED / UNKNOWN）
        - 颜色编码：
            PENDING      → 灰色
            RUNNING      → 蓝色（脉冲动画）
            COMPLETED    → 绿色
            FAILED       → 红色
            ABORTED      → 橙色
            UNKNOWN      → 黄色（警告纹理）
            PENDING_TOOL → 紫色

      点击方块 → 打开侧边抽屉（Drawer）：
        - Job 基本信息（host、device、status_reason）
        - Step 时间线（每个 StepTrace 的状态和耗时）
        - 实时日志面板（WebSocket，见 §3）

底部：聚合统计（完成/失败/未知 数量，刷新倒计时）
```

**数据更新策略**：
- 初始加载：GET /api/v1/workflow-runs/:run_id/jobs
- 实时更新：WebSocket 订阅（见 §3），收到状态变更消息后局部更新对应方块
- 不使用轮询

### 2.3 PipelineEditor（WorkflowDefinition 编辑页内嵌组件）

```
三列布局：prepare | execute | post_process

每列内的 Step 卡片：
  - action 字段：下拉选择 Tool（展示 name + version），自动填充 tool_id
  - params 字段：根据所选 Tool 的 param_schema 动态渲染表单（jsonschema-form 或手写）
  - timeout_seconds：数字输入
  - retry：数字输入（0-5）

保存前校验：
  - 所有 Step 的 params 必须通过 param_schema 校验（前端本地校验）
  - action 字段不允许手动输入，只能从 Tool Catalog 下拉选择
```

## 3. WebSocket 规范

### 连接端点

```
ws://server/ws/workflow-runs/{run_id}    # 订阅整个 WorkflowRun 的状态更新
ws://server/ws/jobs/{job_id}/logs        # 订阅单个 Job 的实时日志
```

### 消息格式（Server → Client）

```typescript
// 状态更新（workflow-runs WebSocket）
interface StatusUpdate {
  type: "job_status" | "step_trace" | "workflow_status";
  job_id?: number;
  status?: string;
  step_id?: string;
  stage?: string;
  event_type?: string;
  timestamp: string;
}

// 日志流（jobs WebSocket）
interface LogLine {
  type: "log";
  level: "INFO" | "WARN" | "ERROR";
  tag: string;
  message: string;
  timestamp: string;
}

// 心跳（防止连接超时）
interface Heartbeat {
  type: "ping";
}
```

### 前端连接管理

```typescript
// 使用 reconnecting-websocket 库处理自动重连
// 最大重连次数：不限（持续重试，每次间隔 2^n 秒，最大 30s）
// WorkflowRun 状态为终态（SUCCESS/FAILED/PARTIAL_SUCCESS/DEGRADED）后，
// 收到最后一条 workflow_status 消息后主动关闭连接

const ws = new ReconnectingWebSocket(
  `${WS_BASE_URL}/ws/workflow-runs/${runId}`,
  [], { maxRetries: Infinity, reconnectionDelayGrowFactor: 2, maxReconnectionDelay: 30000 }
);

ws.onmessage = (event) => {
  const msg = JSON.parse(event.data);
  if (msg.type === "job_status") {
    updateJobBlock(msg.job_id, msg.status);
  } else if (msg.type === "workflow_status" && isTerminalStatus(msg.status)) {
    ws.close();
  }
};
```

## 4. 全局状态管理

使用 **Zustand**（轻量，无需 Redux 复杂度）：

```typescript
// stores/workflowRunStore.ts
interface WorkflowRunStore {
  jobs: Record<number, JobInstance>;          // job_id → JobInstance
  workflowStatus: WorkflowStatus | null;
  updateJob: (job_id: number, patch: Partial<JobInstance>) => void;
  setWorkflowStatus: (status: WorkflowStatus) => void;
}
```

## 5. 错误处理规范

```typescript
// 统一 API 错误处理
async function apiRequest<T>(url: string, options?: RequestInit): Promise<T> {
  const res = await fetch(url, options);
  const body = await res.json();

  if (body.error) {
    // 显示 Toast 通知，包含 error.code 和 error.message
    toast.error(`[${body.error.code}] ${body.error.message}`);
    throw new ApiError(body.error);
  }
  return body.data;
}

// 特殊处理
// INVALID_TRANSITION (409)     → Toast 提示当前状态不允许该操作
// TOOL_NOT_FOUND (422)         → 高亮对应 Step 卡片，提示选择有效工具
// WORKFLOW_DISPATCH_ERROR (400) → 展示详细校验错误，逐字段标红
```
