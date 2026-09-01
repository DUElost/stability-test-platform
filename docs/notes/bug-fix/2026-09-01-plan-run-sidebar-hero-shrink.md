# PlanRun 侧栏：Hero 被 flex 纵向压扁

Status: preview
Class: bug-fix

## Decision

侧栏滚动区是 `flex-col`，子项默认 `flex-shrink: 1`，矮视口下内容被纵向压扁而非滚出。KPI/链包 `shrink-0`；Hero 已移出滚动区固定顶栏（见同日 actions 笔记），高度跟内容走。

顺带把 Hero 标题/meta 间距略放宽（`text-lg`、`gap-y-2`），避免紧致感像「被压矮」。

## Verification

```bash
cd frontend && npx vitest run src/pages/execution/PlanRunDetailPage.test.tsx
npm run build && rm -rf dist-preview && mv dist dist-preview
```

## Revisit

无。
