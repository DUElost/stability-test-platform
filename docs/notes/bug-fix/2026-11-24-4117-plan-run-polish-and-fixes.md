Status: implemented
Class: bug-fix

# PlanRunDetailPage 体验微调、业务流初始化状态修复与非问题 Crash 产物过滤（#4117）

## Decision

结合 Phase 2 生产前端实机验收反馈，在一条内聚变更中收口 6 项 PlanRun 详情页与数据链路问题：

1. **移除 Minimap 异常小红点**（`DeviceOverview.tsx`）：去掉叠加在 Minimap 色块右上角的红色微点，恢复方块阵纯净观感，异常计数仍保留在悬停 `title` 与 `aria-label` 中。
2. **消除派发门禁与业务流进展重复渲染**（`PlanRunDetailPage.tsx`）：移除 Minimap 下方的 `dispatch-gate-section` 与「异常与归档」Tab 底部的重复 `BusinessFlowStepper`，将派发预检/门禁（`PrecheckSummaryRow` + `DispatchGateCard`）与业务流进展唯一收口在「派发与诊断」子栏。
3. **修复业务流进展 `init` 阶段在测试运行期间一直显示「运行中」的 Bug**（`backend/services/plan_run_timeline.py` + `BusinessFlowStepper.tsx`）：
   - 原实现要求 `succeeded >= job_total` 才将 `init` 置为 `completed`，导致只要存在任意设备跳过/中止/缺 trace、或 Plan 无启用 `init` 步骤时，`init` 在整个 `patrol` 运行期间永远卡在 `running`；同时 `patrol` 在未启动前也因 `has_running_jobs=True` 被提前标成 `running`。
   - 改为按每台 `RUNNING` 作业精确判定是否仍有作业停留在 `init`（既未完成全部启用 init 步骤、又未进入 patrol/teardown），无活跃 init 作业且已推进或进入 patrol 时 `init` 收敛为 `completed`；`patrol` 在未出现任何 patrol trace 或心跳前保持 `pending`；前端 `BusinessFlowStepper` 以 `timeline.current_stage` 为高亮真源。
4. **移除按包名筛选饼状图功能**（`AnomalyDashboard.tsx`）：清理 `selectedPackage` 筛选饼图逻辑与「筛饼图」按钮，改为点击任意包名行直接打开右侧抽屉展示受影响设备与 Crash 明细。
5. **优化细分类型占比饼图布局**（`AnomalyDashboard.tsx`）：将原「宽栏居中孤立圆环 + 底部折行碎标签」重构为「左紧凑环形图（动态圆心悬停焦点）+ 右结构化明细排行表（色点 + 类型名 + 精确次数 + 百分比 + 迷你占比条，双向 hover 联动）」。
6. **过滤非问题 AEE 伴生产物**（`backend/agent/watcher/device_watcher.py` + `backend/services/plan_run_job_artifacts.py` + `DeviceDetailDrawer.tsx`）：在 Agent 采集源头、后端读接口与前端抽屉兜底三层统一过滤 `/data/[vendor/]aee_exp` 下的非崩溃伴生文件（`aee.lck`、`*.lck`/`*.lock`/`*.tmp`、`db_history`、`file_tree.txt`、`SWT_JBT_TRACES` 等，自动剥离 `<timestamp>_` 前缀）。

## Alternatives

- **仅在前端隐藏 `init` 运行中态**：治标不治本，API `/timeline` 仍返回错误状态，且导出报告/其他消费者继续读到错误阶段；选择在后端 `plan_run_timeline.py` 根治。
- **仅在 Agent 侧过滤非问题 AEE 文件**：无法清理历史 PlanRun 已落库的 `job_artifact` 行，且前端单独发版前线上仍可见；因此同步补齐后端读侧与前端兜底过滤。

## Verification

- 后端与 Agent 单测：`scripts/run_pytest.py backend/tests/services/test_plan_run_timeline.py backend/tests/api/test_plan_run_aggregation_endpoints.py backend/agent/tests/test_device_watcher_artifact.py -q`
- 前端单测：`npx vitest run src/components/plan-run/DeviceOverview.test.tsx src/components/plan-run/AnomalyDashboard.test.tsx src/components/plan-run/DeviceDetailDrawer.test.tsx src/pages/execution/PlanRunDetailPage.test.tsx`
- 静态守卫与门禁：`scripts/run_pytest.py tests/test_frontend_filter_workbench_guard.py -q`、`scripts/run_gates.py check:quick`

## Revisit

- 后续部署控制面与 Agent 热更新后，历史与新发任务将彻底不再产生非问题 AEE `job_artifact` 记录；前端兜底过滤逻辑可在历史数据全部过期回收后评估精简。
