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
1. 多阶段执行，支持 init（一次性） → patrol（周期循环） → teardown（清理）三阶段
2. 阶段内步骤串行执行，patrol 周期内 best-effort 语义
3. 步骤级别的重试控制（0-5 次），init/teardown 失败即终止
4. 步骤通过环境变量 `STP_STEP_PARAMS` 接收参数（JSON 序列化）
5. 实时日志推送与前端可视化
6. ~~内置 Action 库覆盖设备、进程、文件、日志场景~~（已删除，见下方标注）

## 决策

### 执行模型

> ⚠️ **已废弃 (2026-05-04)**：以下 "Phase 串行 + Step 并行" 模型已被 `lifecycle` 三阶段模型替代（init → patrol loop → teardown）。Phase / `parallel: true` / ThreadPoolExecutor 并行概念已从引擎中移除。唯一合法格式为 `lifecycle` 顶层键，引擎硬性拒绝 `stages` / `phases` 格式（`pipeline_engine.py` L327-350）。详见 [ADR-0020](./ADR-0020-plan-step-one-shot-migration.md)。

采用 **Phase 串行 + Step 并行** 的执行拓扑（历史描述，当前为 lifecycle 模型）：

```
lifecycle（当前模型）
├── init:    [step, step, ...]       # 一次性，失败即终止
├── patrol:  { interval_seconds, steps: [step, ...] }  # 周期循环，best-effort
└── teardown: [step, ...]            # 清理，失败即终止

Pipeline（旧模型，已废弃）
├── Phase 1 (serial/parallel)
│   ├── Step 1.1
│   ├── Step 1.2
│   └── Step 1.3
├── Phase 2 (serial/parallel)
│   ├── Step 2.1
│   └── Step 2.2
└── Phase N ...
```

- **Phase 间**：严格串行（旧模型）
- **lifecycle 三阶段**：init → patrol loop → teardown；init/teardown 步骤串行、失败即终止；patrol 周期内步骤串行、best-effort（不终止整个 pipeline）
- ~~**Phase 内**：可配置 `parallel: true` 使用 ThreadPoolExecutor 并行执行（上限 4 个并发）~~（已废弃，当前无并行选项）
- **整体策略**：init/teardown 为 fail-fast；patrol 周期内失败计入退避连击

### 失败策略

> ⚠️ **部分废弃 (2026-05-04)**：`continue` 策略已不再作为通用选项存在。当前行为：init/teardown 阶段 fail-fast（失败即终止当前阶段及后续阶段）；patrol 周期内 best-effort（步骤失败不终止 pipeline，但计入退避连击，见 [ADR-0022](./ADR-0022-patrol-heartbeat-aggregation.md)）。`retry` 仍支持（0-5 次）。

| 策略 | 行为 | 当前状态 |
|------|------|---------|
| `stop`/fail-fast（默认） | init/teardown: 立即终止整个 Pipeline；patrol: 终止当前周期，进入退避 | ✅ 生效 |
| `continue` | ~~记录失败但继续执行当前 Phase 内剩余步骤~~ | ❌ 已废弃（patrol 周期 best-effort 语义替代） |
| `retry` | 执行 `retry` 次（0-5），固定间隔 | ✅ 生效 |

### Action 类型

> ⚠️ **已废弃 (2026-05-04)**：`builtin:<name>` 和 `tool:<id>` 已从引擎中删除。`_resolve_action()` 仅匹配 `script:<name>` 前缀，其他前缀返回 `None` 并报 `Unsupported action`（`pipeline_engine.py` L791-794, L861-871）。`stages` / `phases` 格式已被引擎硬拒绝，唯一合法格式为 `lifecycle`。脚本能力由 ScriptRegistry（`backend/agent/registry/script_registry.py`）+ Script 目录扫描（`backend/services/script_catalog.py`）替代。详见 [ADR-0020](./ADR-0020-plan-step-one-shot-migration.md)。

| 类型 | 前缀 | 格式 | 说明 | 当前状态 |
|------|------|------|------|---------|
| Built-in | `builtin:<name>` | stages / lifecycle | 内置 Action（21 个） | ❌ **已删除** |
| Tool | `tool:<id>` | stages 仅 | 执行注册的 Tool 脚本（需 ToolRegistry） | ❌ **已删除** |
| ~~Shell~~ | ~~`shell:<command>`~~ | ~~legacy phases 仅~~ | **已废弃** | ❌ 已废弃 |
| **Script** | **`script:<name>`** | **lifecycle** | **ScriptRegistry 解析脚本目录，唯一合法类型** | **✅ 唯一生效** |

### 2026-03-23 决策：不支持 `shell:` action

> ⚠️ **本节前提已变更 (2026-05-04)**：本节论述的 `builtin:` 和 `tool:` 作为 `shell:` 的安全替代方案，此前提已不成立——`builtin:` 和 `tool:` 与 `shell:` 同样已不被引擎支持。当前唯一替代方案为 `script:<name>`，通过 ScriptRegistry 注册制提供审计边界和版本控制。

**背景**：`pipeline_engine.py` 的 legacy `_resolve_action()` 支持 `shell:<command>` 前缀，但 stages 格式的 `_resolve_action_stages()` 明确注释 `no shell: allowed`。JSON Schema (`pipeline_schema.json`) 的 step.action pattern 为 `^(tool:\d+|builtin:.+)$`，不包含 `shell:`。

**决策**：维持现状，**不在 stages/lifecycle 格式中支持 `shell:` action**。

**理由**：
1. **安全边界**：`shell:` 将任意命令嵌入 pipeline JSON，任何能创建工作流的用户即可在目标设备上执行任意命令。~~`builtin:` 和 `tool:` 通过注册机制提供了审计边界~~ → `script:<name>` 通过 ScriptRegistry + 脚本目录扫描提供审计边界
2. **已有替代方案**：~~`builtin:setup_device_commands` / `builtin:run_shell_script` / `tool:<id>`~~ → `script:<name>` 脚本目录机制（见 [ADR-0020](./ADR-0020-plan-step-one-shot-migration.md) §脚本目录与扫描机制）
3. **三层一致**：Engine、Schema、Validator 均不支持 `shell:`，保持一致
4. **模板零使用**：无任何现有 pipeline 模板使用 `shell:` 前缀

### 核心组件

| 组件 | 职责 | 当前状态 |
|------|------|---------|
| `PipelineEngine` | 解析 lifecycle 定义，调度阶段/步骤执行，管理共享状态 | ✅ 生效 |
| `StepContext` | 传递给每个 Action 的执行上下文（ADB、Serial、Params、Logger、Shared） | ✅ 生效 |
| `StepResult` | Action 返回结果（success、exit_code、error_message、metrics） | ✅ 生效 |
| ~~`ACTION_REGISTRY`~~ | ~~内置 Action 函数注册表~~ | ❌ **已删除 2026-05-04** |
| `ScriptRegistry` | 解析 `script:<name>` action，定位脚本文件（nfs_path + script_type） | ✅ 替代 ACTION_REGISTRY |

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
- **步间锁丢失检测**：通过构造函数注入的 `is_aborted` 回调（闭包检查 `_active_job_ids` 集合），在每个 Step 开始前检测 LockRenewalManager 是否因 409 移除了本 job
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

- ✅ PipelineEngine 核心实现（lifecycle 格式）
- ~~✅ 21 个内置 Actions（Device/Process/File/Log/Tool）~~ → ❌ 已删除 2026-05-04，由 Script 目录机制替代
- ✅ 前端 PipelineEditor 可视化编辑器
- ✅ 内置 Pipeline Templates API
- ✅ 设备锁启动前验证 + 步间锁丢失检测
- ✅ `is_aborted` 回调注入（消除 pipeline_engine → main 循环依赖）
- ~~✅ DynamicToolForm 集成到 StepEditorDrawer~~ → ❌ 随 builtin actions 一并移除
- ~~✅ 4 个缺失 action 的 param_schema 补全~~ → ❌ 已不适用
- ✅ 前端必填字段校验 + 后端参数 warning
- ✅ lifecycle 格式硬性校验（拒绝 stages/phases）
- ✅ ScriptRegistry 解析 `script:<name>` action（替代 ACTION_REGISTRY）
- ✅ patrol 心跳聚合 + 退避策略（见 [ADR-0022](./ADR-0022-patrol-heartbeat-aggregation.md)）
- ✅ 超时重试策略配置化 — `PlanStep.timeout_seconds` / `retry` → dispatch payload → engine `_run_step_with_retry()` + `subprocess.communicate(timeout=)`
- ⏳ 长步骤锁丢失中断能力（当前仅在步间检查；长步骤执行中锁丢失仍要等步骤自然结束或超时才能退出，需要 subprocess/线程级信号中断机制）

## 关联实现/文档

### 后端核心
- `backend/agent/pipeline_engine.py` - Pipeline 执行引擎（lifecycle 格式，仅 `script:<name>` action）
- `backend/agent/registry/script_registry.py` - ScriptRegistry（解析 `script:<name>` action）
- ~~`backend/agent/actions/__init__.py` - Action 注册表~~ → ❌ 已删除 2026-05-04
- ~~`backend/agent/actions/device_actions.py` - 设备类 Action~~ → ❌ 已删除
- ~~`backend/agent/actions/process_actions.py` - 进程类 Action~~ → ❌ 已删除
- ~~`backend/agent/actions/file_actions.py` - 文件类 Action~~ → ❌ 已删除
- ~~`backend/agent/actions/log_actions.py` - 日志类 Action~~ → ❌ 已删除
- ~~`backend/agent/actions/tool_actions.py` - Tool 桥接 Action~~ → ❌ 已删除
- `backend/api/routes/pipeline.py` - Pipeline Templates API
- ~~`backend/api/routes/builtin_actions.py` - 内置 Action 及 param_schema API~~ → ❌ 已删除 2026-05-04
- ~~`backend/schemas/builtin_actions.json` - 内置 Action 定义与参数 Schema~~ → ❌ 已删除

### 前端
- `frontend/src/components/pipeline/PipelineEditor.tsx` - lifecycle 可视化编辑器（原 StagesPipelineEditor 已重命名）
- `frontend/src/components/pipeline/PipelineStepTree.tsx` - 运行时步骤树
- ~~`frontend/src/components/task/DynamicToolForm.tsx` - 动态参数表单组件~~ → ❌ 已删除
- ~~`frontend/src/pages/orchestration/WorkflowDefinitionEditPage.tsx` - 工作流编辑页面~~ → 已迁移至 Plan 体系

### API
- `GET /api/v1/pipeline/templates` - 获取内置模板
- `GET /api/v1/pipeline/templates/{name}` - 获取指定模板
- ~~`GET /api/v1/builtin-actions`~~ - **已删除（2026-05-04）**
- ~~`POST /api/v1/workflows` - 创建工作流定义~~ → 已迁移至 `POST /api/v1/plans`
- ~~`POST /api/v1/workflows/{wf_id}/run` - 基于工作流定义触发执行~~ → 已迁移至 `POST /api/v1/plans/{id}/run`

### 数据库
- ~~`Task.pipeline_def` - Pipeline 定义 JSON 字段~~ → 已迁移至 `PlanStep` + `JobInstance.pipeline_def`
- `StepTrace` - 步骤执行追踪表（替代旧 RunStep）

### 关联 ADR
- [`ADR-0003`](./ADR-0003-task-run-state-machine-and-device-lock-lease.md) - 设备锁租约与会话看门狗
- [`ADR-0016`](./ADR-0016-deprecate-base-test-case.md) - 废弃 BaseTestCase

### OpenSpec
- [`archive/openspec/specs/pipeline-engine/spec.md`](../archive/openspec/specs/pipeline-engine/spec.md) - Pipeline 引擎规范（**已归档**，含 deprecated 注）
- [`archive/openspec/changes/archive/2026-03-11-builtin-action-param-forms/`](../archive/openspec/changes/archive/2026-03-11-builtin-action-param-forms/) - 参数表单 Change（已归档）

---

## Deprecated 注（2026-05-04）

ADR-0014 描述的双轨格式（builtin / tool / script + phases/stages/lifecycle）在 2026-05-04 收敛到 **lifecycle + script** 单轨：

- 引擎硬性拒绝 `stages` / `phases` 顶层键，仅接受 `lifecycle`（`pipeline_engine.py` L158-179）
- `_resolve_action` 仅匹配 `script:<name>` 前缀，其他报 `Unsupported action`（`pipeline_engine.py` L427-432）
- 删除项：`backend/agent/actions/`（5 文件 + ACTION_REGISTRY）、`backend/api/routes/builtin_actions.py`、`backend/schemas/builtin_actions.json`、`frontend/.../actionCatalog.ts`、`frontend/.../ActionTemplatePage.tsx`、`api.builtinCatalog`、`BuiltinActionEntry` 类型
- 路由 `/orchestration/actions` 与 `WorkflowDefinitionEditPage` 中"动作目录"按钮一并移除

ADR 主体内容保留作历史决策记录，本注脚仅说明现状偏离。
