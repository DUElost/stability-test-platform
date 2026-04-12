# ADR-0007: 工具配置 + 任务模板 + 工作流扩展模型
- 状态：Accepted
- 日期：2026-02-18（2026-03-24 更新）
- 决策者：平台研发组
- 标签：可扩展性, 工具模型, 工作流, 定时任务

## 背景

稳定性专项（Monkey/MTBF/DDR/GPU/待机等）持续演进，如果每次都改核心调度代码会导致高耦合和交付变慢。

## 决策

采用"三层扩展"模型：

- 工具层：`ToolCategory` + `Tool` 数据化配置，支持脚本路径、参数 Schema、超时等能力。
- 模板层：内置 `task_templates` 提供默认参数与脚本入口。
- 编排层：
  - Workflow 多步骤编排（顺序推进）。
  - Schedule（cron）定时创建任务。

核心调度仅消费统一任务实体，不直接绑定具体专项实现细节。

## 备选方案与权衡

- 方案 A：每个专项写独立路由和独立调度流程。
  - 优点：短期开发快。
  - 缺点：长期形成脚本烟囱，复用差。
- 方案 B：当前方案（配置驱动 + 统一编排）。
  - 优点：扩展成本低，UI 与 API 一致性更好。
  - 缺点：配置治理与参数校验要求更高。

## 影响

- 正向影响：新增专项可通过配置与小规模实现快速接入。
- 代价：工具脚本与参数契约需版本化，否则易出现运行时不兼容。

## 落地与后续动作

- ✅ 已落地：工具 CRUD、扫描同步、工作流 CRUD、cron 调度。
- ✅ Phase 3 路由替代：`tools.py` 和 `workflows.py` 已被 `orchestration.py`、`tool_catalog.py`、`action_templates.py` 替代并在 `main.py` 中挂载。Legacy 路由文件保留但不再挂载。
- ✅ Workflow 执行器重构：`scheduler/workflow_executor.py` 已删除，工作流推进由 `services/dispatcher.py`（`dispatch_workflow`）+ Agent claim 机制替代。
- 后续：建立"工具版本 + 参数 Schema 校验 + 灰度发布"机制。

## 关联实现/文档

### 当前活跃
- `backend/models/tool.py` — Tool ORM 模型（含 `category` 字段，替代旧 `ToolCategory`）
- `backend/core/task_templates.py` — 内置任务模板
- `backend/api/routes/orchestration.py` — Workflow 编排端点（替代 `workflows.py`）
- `backend/api/routes/tool_catalog.py` — 工具目录端点（替代 `tools.py`）
- `backend/api/routes/action_templates.py` — Action 模板端点
- `backend/api/routes/schedules.py` — 定时任务
- `backend/scheduler/cron_scheduler.py` — Cron 调度器
- `backend/services/dispatcher.py` — Workflow 派发服务

### Legacy（保留但未挂载，待 Wave 8 移除）
- `backend/api/routes/tools.py` — 旧工具 CRUD 路由（未在 `main.py` 中 `include_router`）
- `backend/api/routes/workflows.py` — 旧工作流 CRUD 路由（ImportError 占位，未挂载）
