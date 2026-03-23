# ADR-0014: Pipeline 执行引擎架构
- 状态：Accepted
- 优先级：P1
- 目标里程碑：M2
- 日期：2026-02-25（2026-03-16 更新）
- 决策者：平台研发组
- 标签：执行引擎, Pipeline, 任务编排, Agent, 设备锁, 参数表单

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

| 类型 | 前缀 | 格式 | 说明 |
|------|------|------|------|
| Built-in | `builtin:<name>` | stages / lifecycle | 内置 Action（18 个） |
| Tool | `tool:<id>` | stages 仅 | 执行注册的 Tool 脚本（需 ToolRegistry） |
| ~~Shell~~ | ~~`shell:<command>`~~ | ~~legacy phases 仅~~ | **已废弃，不在 stages/lifecycle 中支持**（见下方说明） |

### 2026-03-23 决策：不支持 `shell:` action（stages/lifecycle 格式）

**背景**：`pipeline_engine.py` 的 legacy `_resolve_action()` 支持 `shell:<command>` 前缀，但 stages 格式的 `_resolve_action_stages()` 明确注释 `no shell: allowed`。JSON Schema (`pipeline_schema.json`) 的 step.action pattern 为 `^(tool:\d+|builtin:.+)$`，不包含 `shell:`。

**决策**：维持现状，**不在 stages/lifecycle 格式中支持 `shell:` action**。

**理由**：
1. **安全边界**：`shell:` 将任意命令嵌入 pipeline JSON，任何能创建工作流的用户即可在目标设备上执行任意命令。`builtin:` 和 `tool:` 通过注册机制提供了审计边界
2. **已有替代方案**：
   - 设备端批量命令 → `builtin:setup_device_commands`（参数化，可审计）
   - 主机端脚本 → `builtin:run_shell_script`（执行 Agent 主机上的脚本）
   - 自定义逻辑 → `tool:<id>` Pipeline Action（注册制，版本控制）
3. **三层一致**：Engine（`_resolve_action_stages`）、Schema（`pipeline_schema.json`）、Validator（`pipeline_validator.py`）均不支持 `shell:`，保持一致
4. **模板零使用**：无任何现有 pipeline 模板使用 `shell:` 前缀

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

### 2026-03-16 更新：设备锁验证

PipelineEngine 新增设备锁验证机制，确保 Pipeline 执行期间设备归属正确：

- **启动前验证**：`_verify_device_lock()` 调用 `extend_lock` 端点，携带 `X-Agent-Secret` 鉴权。409 响应表示锁已丢失，Pipeline 立即中止
- **步间锁丢失检测**：通过构造函数注入的 `is_aborted` 回调（闭包检查 `_active_run_ids` 集合），在每个 Step 开始前检测 LockRenewalManager 是否因 409 移除了本 run
- **网络容错**：启动前验证支持指数退避重试（1s, 2s, 4s），区分 401（鉴权失败）和 409（锁丢失）

### 2026-03-16 更新：内置 Action 参数表单

Pipeline Editor 中的步骤参数配置从原始 JSON textarea 升级为 schema 驱动的动态表单：

- **条件渲染**：当 action 为 `builtin:*` 且具有非空 `param_schema` 时，使用 `DynamicToolForm` 组件渲染表单；否则回退到 JSON textarea
- **双向同步**：表单状态与 `step.params` 双向绑定，切换 action 时保留匹配字段值
- **必填校验**：保存前校验 `param_schema.required` 标记的字段，阻止空值提交
- **后端校验**：`pipeline_engine.py` 执行前对照 `param_schema` 校验必填参数，缺失时记录 warning（不阻塞执行）
- **Schema 补全**：为 4 个缺失 schema 的 action 补充了 `param_schema`（`guard_process`、`run_shell_script`、`export_mobilelogs`；`setup_device_commands` 确认无用户参数）

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
- 参数表单降低了 Pipeline 配置门槛，减少 JSON 手写错误

## 落地与后续动作

- ✅ PipelineEngine 核心实现
- ✅ 18 个内置 Actions（Device/Process/File/Log/Tool）
- ✅ 前端 PipelineEditor 可视化编辑器
- ✅ 内置 Pipeline Templates API
- ✅ 设备锁启动前验证 + 步间锁丢失检测
- ✅ `is_aborted` 回调注入（消除 pipeline_engine → main 循环依赖）
- ✅ DynamicToolForm 集成到 StepEditorDrawer
- ✅ 4 个缺失 action 的 param_schema 补全
- ✅ 前端必填字段校验 + 后端参数 warning
- ⏳ 完善超时重试策略配置化
- ⏳ 增加更多内置 Actions（如网络诊断）
- ⏳ 长步骤锁丢失中断能力（当前仅在步间检查）

## 关联实现/文档

### 后端核心
- `backend/agent/pipeline_engine.py` - Pipeline 执行引擎（含锁验证）
- `backend/agent/actions/__init__.py` - Action 注册表
- `backend/agent/actions/device_actions.py` - 设备类 Action
- `backend/agent/actions/process_actions.py` - 进程类 Action
- `backend/agent/actions/file_actions.py` - 文件类 Action
- `backend/agent/actions/log_actions.py` - 日志类 Action
- `backend/agent/actions/tool_actions.py` - Tool 桥接 Action
- `backend/api/routes/pipeline.py` - Pipeline Templates API
- `backend/api/routes/builtin_actions.py` - 内置 Action 及 param_schema API
- `backend/data/builtin_actions.json` - 内置 Action 定义与参数 Schema

### 前端
- `frontend/src/components/pipeline/PipelineEditor.tsx` - 可视化编辑器
- `frontend/src/components/pipeline/StagesPipelineEditor.tsx` - Stages 编辑器（含 DynamicToolForm 集成）
- `frontend/src/components/pipeline/actionCatalog.ts` - Action 目录
- `frontend/src/components/pipeline/pipelineTypes.ts` - 类型定义
- `frontend/src/components/pipeline/PipelineStepTree.tsx` - 运行时步骤树
- `frontend/src/components/tools/DynamicToolForm.tsx` - 动态参数表单组件
- `frontend/src/pages/tasks/CreateTask.tsx` - 任务创建集成

### API
- `GET /api/v1/pipeline/templates` - 获取内置模板
- `GET /api/v1/pipeline/templates/{name}` - 获取指定模板
- `GET /api/v1/builtin-actions` - 获取内置 Action 列表（含 param_schema）
- `POST /api/v1/tasks` - 创建任务（含 `pipeline_def` 字段）

### 数据库
- `Task.pipeline_def` - Pipeline 定义 JSON 字段
- `RunStep` - 步骤执行记录表

### 关联 ADR
- [`ADR-0003`](./ADR-0003-task-run-state-machine-and-device-lock-lease.md) - 设备锁租约与会话看门狗
- [`ADR-0016`](./ADR-0016-deprecate-base-test-case.md) - 废弃 BaseTestCase

### OpenSpec
- [`openspec/specs/pipeline-engine/spec.md`](../../openspec/specs/pipeline-engine/spec.md) - Pipeline 引擎规范（含锁验证）
- [`openspec/changes/builtin-action-param-forms/`](../../openspec/changes/builtin-action-param-forms/) - 参数表单 Change
