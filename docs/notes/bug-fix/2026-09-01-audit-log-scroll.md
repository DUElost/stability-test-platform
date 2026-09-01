# 审计日志页无法滚动

Status: preview
Class: bug-fix

## Decision

与 PlanRun 日志页同类：`PANEL.root` 的 `overflow-hidden` + `overflow-x-auto` 在中间层吞滚轮，外层 `PageContainer` 滚不动。

改为 `PageContainer scrollable={false}`，表格区 `flex-1 min-h-0 overflow-auto`，筛选/分页 `shrink-0` 钉住。

## Verification

```bash
cd frontend && npx vitest run src/components/layout/PageContainer.test.tsx
```

目视 `/audit`：筛选固定，表格可纵向滚动，分页仍可见。

## Revisit

`SchedulesPage` 同类 `PANEL + overflow-x-auto` 若反馈再改。
