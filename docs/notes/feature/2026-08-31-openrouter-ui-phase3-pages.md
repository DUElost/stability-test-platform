# OpenRouter UI Phase 3：Dashboard / 列表页稀疏化

Status: implemented
Class: feature

## Decision

在 Phase 2 外壳之上做第一批逐页形态（#464）：**图表行 + 稀疏数字卡**，密度四档不变。

- `STAT`：mono 大数字、小写标签、8×8 图标槽（弃 48px 色块井）。
- `DashboardStatCard`：标签在上、数字在下；hover 改 `bg-muted/30`，去 `shadow-md`。
- Dashboard：KPI 行收紧；**活动趋势 / 通过率趋势全宽置顶**，其余 2 列栅格下沉。
- Plan 列表 / 项目登记簿 / 结果页：KPI 条与列表卡对齐稀疏样式，去浮起 hover。

涉及：`tokens.ts`、`DashboardStatCard`、`Dashboard`、`PlanListPage`、`ProjectsPage`、`ResultsPage`。

## Alternatives

- 一次改完全部资源/详情页：回归面过大，留给后续 batch。
- 去掉 KPI 图标：信息密度损失，保留小号井。

## Verification

```bash
cd frontend && npm run type-check && npm run lint && npx vitest run && npm run build
```

肉眼：仪表盘数字变「终端账单」感；趋势图先于饼/柱；列表卡不再浮起。

## Revisit

- 资源页（Hosts/Devices）与 PlanRun 详情密度。
- Phase 4：mockups `plan-execute-v2/styles.css` 同步。
