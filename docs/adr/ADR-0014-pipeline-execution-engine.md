# ADR-0014: Pipeline 执行引擎架构
- 状态：Accepted
- 优先级：P1
- 目标里程碑：M2
- 日期：2026-02-25
- 决策者：平台研发组
- 标签：执行引擎, Pipeline, 任务编排, Agent

## 背景

平台需要支持更灵活的任务执行流程，超越传统的单一脚本执行模式。
要求支持：
1. 多阶段（Phase）串行执行，每个阶段包含多个步骤（Step）
2. 阶段内步骤可并行或串行执行
3. 步骤级别的失败策略控制（stop/continue/retry）
4. 步骤间数据共享（如启动进程后获取 PID）
5. 实时日志推送与前端可视化
6. 内置 Action 库覆盖设备、进程、文件、日志场景

## 决策

### 执行模型

采用 **Phase 串行 + Step 并行** 的执行拓扑：

```
Pipeline
├── Phase 1 (serial/parallel)
│   ├── Step 1.1
│   ├── Step 1.2
│   └── Step 1.3
├── Phase 2 (serial/parallel)
│   ├── Step 2.1
│   └── Step 2.2
└── Phase N ...
```

- **Phase 间**：严格串行，前一阶段完全执行完毕后才进入下一阶段
- **Phase 内**：可配置 `parallel: true` 使用 ThreadPoolExecutor 并行执行（上限 4 个并发）
- **整体策略**：`on_failure: stop` 时立即终止整个 Pipeline

### 失败策略

| 策略 | 行为 |
|------|------|
| `stop`（默认） | 立即停止当前 Phase 内剩余步骤 + 取消后续所有 Phase |
| `continue` | 记录失败但继续执行当前 Phase 内剩余步骤 |
| `retry` | 执行 `max_retries` 次重试，固定 5s 间隔 |

### Action 类型

| 类型 | 前缀 | 说明 |
|------|------|------|
| Built-in | `builtin:<name>` | 内置 Action（18 个） |
| Tool | `tool:<id>` | 执行注册的 Tool 脚本 |
| Shell | `shell:<command>` | 直接执行 ADB shell 命令 |

### 核心组件

| 组件 | 职责 |
|------|------|
| `PipelineEngine` | 解析 Pipeline 定义，调度 Phase/Step 执行，管理共享状态 |
| `StepContext` | 传递给每个 Action 的执行上下文（ADB、Serial、Params、Logger、Shared） |
| `StepResult` | Action 返回结果（success、exit_code、error_message、metrics） |
| `ACTION_REGISTRY` | 内置 Action 函数注册表 |

### 实时通信

- **WebSocket**：步骤状态（RUNNING → COMPLETED/FAILED/CANCELED）实时推送
- **HTTP Fallback**：WebSocket 不可用时降级到 HTTP 轮询
- **Log Fold Groups**：使用 OSC 633 协议发送折叠标记，前端渲染为可折叠日志区域

### 超时与取消

- **步骤超时**：使用 daemon thread 实现，超时后放弃 worker thread
- **Pipeline 取消**：`cancel()` 方法设置标志位，阻止后续 Phase/Step 启动

## 备选方案与权衡

- 方案 A：使用外部工作流引擎（如 Airflow、Prefect）
  - 优点：功能丰富，生态成熟
  - 缺点：引入外部依赖，与 Agent 部署耦合复杂
- 方案 B：自定义轻量级 Pipeline 引擎（当前决策）
  - 优点：无外部依赖，与 Agent 紧耦合，部署简单
  - 缺点：需自行实现重试、超时、并行调度

## 影响

- 正向影响：任务编排能力大幅提升，支持复杂测试场景
- 需维护内置 Action 库与前端编辑器同步
- 状态上报频率增加，需考虑 WebSocket 连接稳定性

## 落地与后续动作

- ✅ PipelineEngine 核心实现
- ✅ 18 个内置 Actions（Device/Process/File/Log/Tool）
- ✅ 前端 PipelineEditor 可视化编辑器
- ✅ 内置 Pipeline Templates API
- ⏳ 完善超时重试策略配置化
- ⏳ 增加更多内置 Actions（如网络诊断）

## 关联实现/文档

### 后端核心
- `backend/agent/pipeline_engine.py` - Pipeline 执行引擎
- `backend/agent/actions/__init__.py` - Action 注册表
- `backend/agent/actions/device_actions.py` - 设备类 Action
- `backend/agent/actions/process_actions.py` - 进程类 Action
- `backend/agent/actions/file_actions.py` - 文件类 Action
- `backend/agent/actions/log_actions.py` - 日志类 Action
- `backend/agent/actions/tool_actions.py` - Tool 桥接 Action
- `backend/api/routes/pipeline.py` - Pipeline Templates API

### 前端
- `frontend/src/components/pipeline/PipelineEditor.tsx` - 可视化编辑器
- `frontend/src/components/pipeline/actionCatalog.ts` - Action 目录
- `frontend/src/components/pipeline/pipelineTypes.ts` - 类型定义
- `frontend/src/components/pipeline/PipelineStepTree.tsx` - 运行时步骤树
- `frontend/src/pages/tasks/CreateTask.tsx` - 任务创建集成

### API
- `GET /api/v1/pipeline/templates` - 获取内置模板
- `GET /api/v1/pipeline/templates/{name}` - 获取指定模板
- `POST /api/v1/tasks` - 创建任务（含 `pipeline_def` 字段）

### 数据库
- `Task.pipeline_def` - Pipeline 定义 JSON 字段
- `RunStep` - 步骤执行记录表
