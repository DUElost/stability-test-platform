# PlanRun Hero：标题与状态块两行对齐

Status: preview
Class: bug-fix

## Decision

侧栏 Hero 标题改为两行「PlanRun」+「#id」；右侧状态块增高加宽（`text-sm` 状态 + `text-xs` 时长），与左侧两行对齐，避免原先单行 `PlanRun #id` 旁小 pill 显得过挤。

## Verification

```bash
cd frontend && npx vitest run src/pages/execution/PlanRunDetailPage.test.tsx
```

目视详情侧栏：左 PlanRun/#327，右 FAILED / 时长块。

## Revisit

无。
