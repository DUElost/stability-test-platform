# 列表页密度：执行记录 / 问题追踪 / 脚本库 / 用例套件

Status: preview
Class: feature

## Decision

沿仪表盘密度约定收口四页：

1. **执行记录**：表格 + 状态 Tab + 搜索 + 分页；`GET /plan-runs` 返回 `{items,total,stats}`；项目筛从页头挪到工具条。
2. **问题追踪**：`LAYOUT.pageGap`；刷新仅草稿 Tab 工具条；草稿改表格；去掉重复 Card/说明卡。
3. **脚本库**：`LAYOUT.pageGap`；卡片堆叠改表格，参数/使用统计行内展开。
4. **用例套件**：`LAYOUT.pageGap`；新建/项目/搜索同工具条；列表改表格。
5. **Plan 编排**：项目/专项筛出页头进工具条；卡片堆叠改按项目分组的表格；KPI 用 `DashboardStatCard`。

## Verification

```bash
cd frontend && npx vitest run \
  src/pages/execution/PlanRunListPage.test.tsx \
  src/pages/issues/IssueTrackerPage.test.tsx \
  src/pages/suites/TestSuitesPage.test.tsx \
  src/pages/orchestration/PlanListPage.test.tsx
JWT_SECRET_KEY=test-secret python -m pytest backend/tests/api/test_plan_runs_api.py -q --tb=line
npm run build && rm -rf dist-preview && mv dist dist-preview
```

## Revisit

脚本表分类 Tab；问题追踪草稿 N+1 拉 draft 另开性能项。
