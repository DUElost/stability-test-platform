# PlanRun 详情：侧栏操作条固定底部

Status: preview
Class: bug-fix

## Decision

左侧栏「导出报告 / 中止运行 / 复跑」从 Hero 卡片内挪到侧栏 **sticky 底栏**（`shrink-0 border-t`），上方 Hero+KPI+链可滚；矮视口下操作钮不再被挤出可视区。

导出菜单在视口下方空间不足时改为向上展开。

## Verification

```bash
cd frontend && npx vitest run src/pages/execution/PlanRunDetailPage.test.tsx
npm run build && rm -rf dist-preview && mv dist dist-preview
```

## Revisit

若底栏与移动端抽屉手势冲突再调 `pb-safe`。
