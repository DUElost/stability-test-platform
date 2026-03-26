# ADR-0016: 废弃 BaseTestCase，以 Pipeline Action 体系为唯一执行模型
- 状态：Accepted
- 优先级：P0
- 目标里程碑：M2
- 日期：2026-03-10
- 决策者：平台研发组
- 标签：执行引擎, Pipeline, BaseTestCase, 废弃

## 背景

平台早期引入了 `BaseTestCase`（`backend/agent/test_framework.py`）作为测试脚本的基础类，提供 stage 流程管理、HTTP 心跳上报、日志缓冲等能力。

随着 Pipeline 执行引擎（ADR-0014）的落地，所有执行能力已由以下两类 Action 完整覆盖：

| Action 类型 | 前缀 | 适用场景 |
|------------|------|---------|
| 内置 Action | `builtin:<name>` | 设备操作、进程管理、日志采集等通用能力 |
| Tool Action | `tool:<id>` | 注册的自定义 Pipeline Action 脚本 |

> **注意**：`shell:<command>` 仅在 legacy phases 格式中残留，stages/lifecycle 格式**不支持**（详见 ADR-0014 决策）。新增 Action 必须使用 `builtin:` 或 `tool:` 前缀。

`BaseTestCase` 体系存在以下根本性问题：

1. **单体执行模型**：整个测试逻辑封装在单一 `run()` 调用中，无法复用平台的 Phase/Step 并行调度、重试策略、步骤级状态追踪。
2. **私有日志通道**：通过 HTTP 心跳上报日志，与平台 Redis Streams → WebSocket 实时日志链路割裂，前端无法实时展示执行过程。
3. **不可组合**：`BaseTestCase` 脚本无法与 `builtin:` action 混合编排成一个 Pipeline，限制了测试场景的灵活组合。
4. **重复维护负担**：`stage`、`retry`、`timeout`、`on_failure` 等能力在 `PipelineEngine` 中已有完整实现，`BaseTestCase` 中的同类实现构成冗余代码。

## 决策

**即日起，`BaseTestCase` 体系正式废弃。所有新增及迁移的测试脚本必须遵循 Pipeline Action 规范。**

### 核心规则

1. **禁止新增**：禁止新建任何继承自 `BaseTestCase` 的类。
2. **禁止桥接**：`PipelineEngine` 不得为 `BaseTestCase` 提供任何适配层（如 `_is_base_test_case()`、`_run_base_test_case()`）。bridging 代码一旦出现，须立即回滚。
3. **强制迁移路径**：现有 `BaseTestCase` 脚本按以下路径迁移：
   - **可通用化的能力** → 实现为 `builtin:` Action（位于 `backend/agent/actions/`）
   - **不可通用化的专项逻辑** → 实现为 `tool:` Action（原生 Pipeline Action 类，`run(ctx: StepContext) -> StepResult` 接口）
   - **参数差异** → 通过 `pipeline_def` 中的 `params` 字段传入，不在代码中 hardcode
4. **文件保留但冻结**：`backend/agent/test_framework.py` 暂时保留（避免删除引发隐性依赖），但：
   - 文件顶部已标注 `# DEPRECATED` 声明
   - 不得向该文件添加任何新功能
   - 不得在新代码中 `import` 该模块

### Pipeline Action 规范接口

所有 `tool:<id>` 脚本必须实现以下接口：

```python
from backend.agent.pipeline_engine import StepContext, StepResult

class MyAction:
    def run(self, ctx: StepContext) -> StepResult:
        # ctx.adb      — AdbWrapper，执行设备命令
        # ctx.serial   — 设备序列号
        # ctx.params   — pipeline_def 中传入的参数字典
        # ctx.run_id   — 当前 WorkflowRun ID
        # ctx.logger   — StepLogger，日志实时推送到前端
        # ctx.shared   — 跨 Step 共享数据（同一 Run 内有效）
        # ctx.local_db — Agent LocalDB，跨 Run 持久化状态
        ...
        return StepResult(success=True)
```

**禁止使用**：`BaseTestCase`、`TestResult`、`TestStage`、`_maybe_send_heartbeat()`、HTTP 心跳上报。

### 增量状态持久化

`StepContext.shared` 仅在单次 Workflow Run 内有效。跨 Run 的持久化状态必须使用 `ctx.local_db`（Agent 侧 SQLite WAL），禁止使用 `BaseTestCase` 中的 `_log_buffer` / JSON 文件等私有方式。

## 备选方案与权衡

- **方案 A：保留 BaseTestCase，提供 PipelineEngine 桥接适配层**
  - 优点：存量脚本零改动即可接入
  - 缺点：两套执行模型长期共存，前端日志链路割裂，步骤级可见性无法实现；桥接层引入隐性约定，新加入的开发者容易误用
  - **决策：拒绝。此方案已在 2026-03-10 实验性实现并回滚。**

- **方案 B：完全删除 test_framework.py**
  - 优点：彻底消除误用可能
  - 缺点：可能存在尚未迁移的调用点，删除会导致运行时错误
  - **决策：暂缓。文件保留但冻结，待存量迁移完成后执行删除。**

- **方案 C（当前决策）：冻结 + ADR 明确禁止 + 强制迁移路径**
  - 优点：平稳过渡，不影响当前在运行的任务；ADR 为 AI 和人类开发者提供明确约束
  - 缺点：需要人工跟踪迁移进度

## 影响

- **开发规范**：所有新增 Action 只使用 `builtin:` 或 `tool:` 两种路径，`tool:` 脚本统一实现 `run(ctx) -> StepResult` 接口。
- **代码审查**：PR 中出现 `BaseTestCase`、`TestStage`、`TestResult` 的新引用应被拒绝。
- **AI 辅助开发**：AI（Cursor、Claude 等）在生成或修改 Agent 侧代码时，必须首先检查本 ADR，禁止生成 `BaseTestCase` 相关代码或桥接逻辑。

## 落地与后续动作

- ✅ 回滚 PipelineEngine 中的 BaseTestCase 桥接代码（`_is_base_test_case`、`_run_base_test_case`）
- ✅ `test_framework.py` 文件顶部添加 DEPRECATED 声明
- ✅ 本 ADR 录入，明确废弃边界和迁移路径
- ⏳ 审计存量使用：`grep -r "BaseTestCase" backend/ --include="*.py"`，逐一迁移
- ⏳ 存量迁移完成后，删除 `backend/agent/test_framework.py`

## 关联实现/文档

- `backend/agent/test_framework.py` — 已冻结的废弃模块
- `backend/agent/pipeline_engine.py` — 当前唯一执行引擎
- `backend/agent/actions/` — 内置 Action 库
- [`ADR-0014`](./ADR-0014-pipeline-execution-engine.md) — Pipeline 执行引擎架构（上层决策）
- [`openspec/changes/archive/2026-03-04-aee-script-migration-to-builtin-actions/proposal.md`](../../openspec/changes/archive/2026-03-04-aee-script-migration-to-builtin-actions/proposal.md) — AEE 脚本迁移方案（具体迁移案例）
