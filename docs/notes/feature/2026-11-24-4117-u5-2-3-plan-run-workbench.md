# Agent Note: U5-2 / U5-3 `PlanRunDetailPage` 主工作台分层与 `DeviceDetailDrawer` 标杆对齐（#4117）

Status: implemented
Class: feature

## Decision

- 落实 `#4117` Phase 2 Track A 中 `PlanRunDetailPage` 的核心盯盘体验升级（`U5-2` + `U5-3`），严格遵守 Owner 指定的「保留 Minimap 设定」硬约束：
  1. **Minimap 常驻置顶 + 轻量增强（`DeviceOverview.tsx`）**：保留每设备 1 方块的经典 Minimap 视图位于主区最顶部；为带 Watcher 异常计数的设备增加右上角微点提示，并为当前 `DeviceDetailDrawer` 打开的设备方块保持选中高亮边框。
  2. **下方 8 张重型板块按阶段分层（`PlanRunDetailPage.tsx`）**：将原纵向 9 卡长瀑流收口为 3 个清晰二级页签（`?section=monitoring|delivery|diagnostics`）：默认「异常与进度」（`AnomalyDashboard` / `ArchiveStatusCard` / `BusinessFlowStepper`）、「日志与交付」（终态 `LogEventsCard` / `TestCaseResultsCard` / `DedupReportCard` / `JiraRunHistory`，按需挂载减少运行期无效请求）与「派发与诊断」；同时保留准入预检/派发故障摘要条在页签上方常显，确保门禁失败不被隐藏。
  3. **左侧 `PlanRunKpiGrid` 压实并支持点击联动筛选**：压缩卡片垂直高度释放 `PlanChainSidebar` 可见空间，支持点击「设备总数 / 运行中 / 已完成 / 失败 / Job 失联·退避 / 设备连接异常」直接联动筛选右侧 Minimap 与设备表。
  4. **PlanRun `DeviceDetailDrawer` 对齐 Phase 1 标杆规格（`U5-3`）**：宽度升至 `size="lg"`；顶栏新增 `< 上一台 / 下一台 >` 切机导航与一键复制串号；抽屉开关同步至 URL `?drawer=<jobId>`；底栏补齐「交给 AI 诊断」（契约 §4.3）与「单机日志」深链；修正 `:257` 陈旧文案与 `:217` `text-destructive-text` WCAG AA 对比度。
  5. **残余壳层与控件收口**：`PlanRunHero` 导出菜单由手写 `fixed inset-0` 升级为 Radix `DropdownMenu`；无效 ID 与加载错误态纳入 `PageContainer width="content"` 并支持优先还原 `location.state.from`。

## Alternatives

- **用吸顶锚点条替代二级页签**：虽然一次可滚览全页，但长稳运行期与终态下 9 张重型卡（含 `JiraRunHistory` 与 `LogEventsCard`）同时挂载会导致首屏与滚屏负担及多余 API 请求；采用 Minimap 置顶 + 3 个阶段二级页签既保留全局态势直觉，又让运行态与交付态各得其所。

## Verification

- Vitest 单测覆盖：`PlanRunDetailPage.test.tsx`、`PlanRunKpiGrid.test.tsx`、`DeviceDetailDrawer.test.tsx`、`DeviceOverview.test.tsx`、`AnomalyDashboard.test.tsx`。
- 静态契约守卫：`tests/test_frontend_filter_workbench_guard.py` 与 `scripts/run_gates.py check:quick` 全绿。

## Revisit

- 合并后在 `stp-ui-review` 对 `/execution/plan-runs/:runId` 做双视口（`1440×900` / `1280×800`）× 双主题实机截图审阅并回填 `#4117`。
