# Plan → Step 编排模型重构：背景与设计理由

## 现状

当前编排模型采用五层嵌套抽象：

```
Workflow → TaskTemplate → PipelineDef → Phase → Step
```

用户在前端编排一个测试流程时面对的概念：

- **Workflow**（工作流）：顶层容器，包含基本信息、失败阈值、Setup/Teardown Pipeline
- **TaskTemplate**（任务模板）：一个 Workflow 下可包含多个模板，每个模板有独立的 Pipeline 定义
- **PipelineDef**（Pipeline 定义）：JSONB 格式，内含 lifecycle { init, patrol, teardown }
- **Phase**（阶段）：init / patrol / teardown，步骤的组织维度
- **Step**（步骤）：实际执行的脚本引用

此外，Workflow 级别还有两个独立的 JSONB 字段：`setup_pipeline` 和 `teardown_pipeline`，在执行时与 TaskTemplate 的 Pipeline 合并。

对应的前端编辑器页面（`WorkflowDefinitionEditPage`，817 行）纵向堆叠了 6 张卡片：基本信息 → 执行全景图 → 任务模板列表 → Setup 编辑器 → Task 编辑器 → Teardown 编辑器。每个编辑器（`StagesPipelineEditor`，977 行）内部还有 Focus/All 模式切换、抽屉式步骤编辑、拖拽排序等交互。

## 问题

**概念层级过深。** 用户需要理解 5 层嵌套关系才能完成编排。尤其"TaskTemplate"这一层——为什么要在一个 Workflow 下建多个模板？Setup/Teardown Pipeline 和模板的 Pipeline 是什么关系？这些问题反复被问到。

**同一页面承载过多独立编辑器。** Setup Pipeline 编辑器、Task Pipeline 编辑器（还带模板切换）、Teardown Pipeline 编辑器三者并排，用户在一个页面上操作三套独立的状态，滚动疲劳严重。

**TaskTemplate 列表的存在意义模糊。** 实际使用时，绝大多数 Workflow 只有一个 TaskTemplate。多模板场景反而是例外，却作为常规 UI 元素始终占据空间。

**Setup/Teardown Pipeline 是二等公民。** 它们在 Workflow 级别以 JSONB 形式存在，与 TaskTemplate 的 Pipeline 在执行时由 `_resolve_pipeline()` 合并。这种"拼接"逻辑增加了后端复杂度和调试难度。

**编辑器交互打断上下文。** 点击步骤编辑弹出一个全屏右侧抽屉，完全遮住 Pipeline 视图。用户看不到该步骤在流程中的位置。

**参数编辑错位。** 当前编排器允许在步骤上编辑 JSON 参数。但参数实际上是脚本的属性，应该在脚本管理页面维护，编排器只做引用和展示。

## 目标模型

```
Plan → Step (phase = init | patrol | teardown)
```

一个 Plan = 一个完整的测试计划（如"Monkey 稳定性测试"、"DDR 专项测试"）。

- Plan 直接包含 Steps，不再有 TaskTemplate 或 PipelineDef 中间层
- Step 按 `phase` 列分为 init / patrol / teardown 三组
- `plan.patrol_interval_seconds` 决定是否有 Patrol 阶段（NULL = 无）
- `plan.next_plan_id` 支持 Plan 间链式触发：Monkey 测试完成后自动启动 DDR 测试

### 为什么不是 Plan → Block → Step

最初的设计包含 Block 层（Plan 包含多个 Block，每个 Block 有 Init/Patrol/Teardown）。经讨论后去掉，原因：

- 多 Block 的场景（如"设备准备"+"Monkey 压测"+"环境清理"）可以用 Plan 链式执行替代，语义更清晰
- 一个 Plan 内只做一件事，符合单一职责直觉
- 减一层就少一层理解和维护成本

### Plan 链 vs Plan 内多 Block

| 场景 | 方案 |
|------|------|
| Monkey 测试跑完后跑 DDR 测试 | 两个 Plan，通过 next_plan_id 串联 |
| Monkey 测试前需要设备准备 | 设备准备作为 Monkey Plan 的 Init 阶段步骤 |
| Monkey 测试后需要清理环境 | 清理环境作为 Monkey Plan 的 Teardown 阶段步骤 |
| 多个 Plan 共用同一批设备 | 链式执行自动继承设备列表 |

## 对比

| 维度 | 旧模型 | 新模型 |
|------|--------|--------|
| 抽象层数 | 5 | 2（Plan → Step） |
| 前端页面 | 单页 6 卡片堆叠 + 3 个独立编辑器 | 单页 3 个 phase 区域 + 1 个步骤列表 |
| 步骤编辑 | 全屏抽屉遮挡上下文 | 点击行选中，右栏展示属性 |
| 参数维护 | 编排器中编辑 JSON | 脚本管理页面维护，编排器只读展示 |
| 多任务支持 | 一个 Workflow 包含多个 TaskTemplate | 多个 Plan 通过 next_plan_id 串联 |
| Setup/Teardown | Workflow 级别的特殊 JSONB 字段 | Plan 的 Init/Teardown 阶段中的步骤 |
| Agent 端影响 | — | 零变化（pipeline_def 格式不变） |

## 配套变更

- **脚本管理**（`ScriptLibraryPage`）：从只读目录升级为可编辑的参数管理页面（参数 schema 编辑、默认值设置、版本管理）
- **编排器**：新建 `PlanEditPage`，替代 `WorkflowDefinitionEditPage` + `StagesPipelineEditor`
- **派发流程**：去掉 per-step override 弹窗，改为"选 Plan → 选设备 → 确认执行"三步
