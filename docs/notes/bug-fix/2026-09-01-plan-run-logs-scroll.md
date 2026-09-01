# PlanRun 日志页无法滚动

Status: accepted
Class: bug-fix

## Decision

`AppShell` main 是 `overflow-hidden`；日志页 `PageContainer` 依赖外层 `overflow-auto`，但事件面板用了 `PANEL.root` 的 `overflow-hidden`，滚轮被内层吞掉、页面看起来不能滚。

改为：`PageContainer scrollable={false}` + 事件流 `flex h-full min-h-0 flex-col`，列表区 `flex-1 overflow-y-auto`；过滤条/分页钉住。顺带给 `PageContainer` 加 `min-h-0`，避免 flex 子项默认 `min-height:auto` 撑破高度链。

## Verification

```bash
cd frontend && npx vitest run src/pages/execution/PlanRunLogsPage.test.tsx src/components/plan-run/PlanRunEventStream.test.tsx src/components/layout/PageContainer.test.tsx
```

目视 `/execution/plan-runs/<id>/logs`：事件列表可滚，过滤与分页仍可见。

## Revisit

无。
