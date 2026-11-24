# 细分类型占比饼图垂直居中与包名榜抽屉行内原地展开（#4117）

Status: implemented
Class: bug-fix

## Decision

1. **饼图卡片垂直居中（`AnomalyDashboard.tsx`）**：为「本次新增」与「运行前遗留」的「细分类型占比」左栏卡片添加 `flex flex-col` 与 `flex-1 items-center justify-center` 包裹层，并将环形图尺寸微调至 `216px × 216px`（`innerRadius=64, outerRadius=92`），消除右侧 Top 5 包名榜撑高整行后左卡下半区的大片留白。
2. **包名榜抽屉行内原地展开（`AnomalyDashboard.tsx`）**：移除 `PackageRankingDrawer` 顶部的独立 `CrashDetailPanel`，改为在所点击的 `drawer-row-<package>` 卡片内部下方原地展开详情面板（带 `ChevronDown` 展开指示与 `scrollIntoView({ block: 'nearest' })` 辅助滚动），并在从外部直接点击某包名打开抽屉时自动定位至其所在分页，避免点击底部包名后需要滚回顶部查看详情。

## Alternatives

- **点击包名后自动滚动抽屉回顶部**：会打断用户浏览排名列表的上下文位置；原地手风琴展开更符合列表项查看详情的直觉预期。

## Verification

- `npx vitest run src/components/plan-run/AnomalyDashboard.test.tsx`
- `scripts/run_pytest.py tests/test_frontend_filter_workbench_guard.py -q`
- `scripts/run_gates.py check:quick`

## Revisit

- 如未来包名榜接入服务端分页，外部指定包名直达时需由后端或查询参数支持按包名定位页码。
