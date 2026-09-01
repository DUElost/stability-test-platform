# PlanRun 侧栏：Hero 被 flex 纵向压扁

Status: preview
Class: bug-fix

## Decision

侧栏滚动区是 `flex-col`，子项默认 `flex-shrink: 1`，矮视口下 Hero/KPI/链被纵向压扁而非滚出。给三者包一层 `shrink-0`，高度跟内容走，由侧栏滚动消化溢出。

顺带把 Hero 标题/meta 间距略放宽（`text-lg`、`gap-y-2`），避免紧致感像「被压矮」。

## Verification

```bash
cd frontend && npx vitest run src/pages/execution/PlanRunDetailPage.test.tsx
npm run build && rm -rf dist-preview && mv dist dist-preview
```

## Revisit

无。
