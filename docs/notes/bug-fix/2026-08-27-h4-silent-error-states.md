# H4 修复：三处子数据失败被静默吞空

- **日期**：2026-08-27
- **关联**：`docs/reviews/FRONTEND_UI_REVIEW_2026-08-27.md` H4（复核轮定级：中危，误导性 UX）
- **类型**：bug-fix（三态补全）

## 决定了什么

三处"query 失败静默成空数据/空卡"改为可识别提示：

1. **DeviceMetricsModal**（`pages/devices/components/DeviceMetricsModal.tsx`）：
   取 `isError`/`refetch`，失败在弹窗内显示 `InlineError` + 重试（原 `data?.points || []`
   静默渲染空图表）。
2. **ArchiveStatusCard**（`components/plan-run/ArchiveStatusCard.tsx`）：
   Props 补 `isLoading`/`isError`/`onRetry`；`PlanRunDetailPage` 传 `watcherQ` 状态。
   - isError → 卡壳 + `InlineError` + 重试
   - isLoading 或真空（`!opsMetrics`）→ 卡壳 + "暂无数据"占位
   - **原 `!opsMetrics → return null`（失败/加载/真空整卡消失）移除**——卡不再静默消失
3. **PlanExecutePage**（`pages/execution/PlanExecutePage.tsx`）：
   hostsList / scriptsList / recentPlanRuns 三个 query 取 `isError`/`refetch`；
   页面顶部（PageHeader 下、ExecuteCommandBar 上）按失败源渲染 `ALERT_BANNER.destructive`
   提示条 + 重试。plans/devices 原本就有完整错误面，本次只补这三处辅助数据。

## 放弃的备选

- ArchiveStatusCard 失败时整卡隐藏：被否（正是本次要消灭的静默形态）。
- 为三个 query 各自在功能区内嵌提示（如墙钟区、容量区）：成本高、侵入深，改为页面顶部
  聚合提示条（信息完整、一处插入），后续如需可再细化。

## 如何验证

- `eslint` + `tsc --noEmit` 全过。
- vitest：`PlanRunDetailPage.test.tsx` + `DevicesPage.test.tsx` + `PlanExecutePage.test.tsx`
  3 文件 76 用例全过（ArchiveStatusCard 无既有断言，行为变更无回归）。

## 何时重议

- 无。若产品希望 PlanExecutePage 的提示从"顶部聚合"细化为"墙钟区/容量区内联"，再按区拆分。
