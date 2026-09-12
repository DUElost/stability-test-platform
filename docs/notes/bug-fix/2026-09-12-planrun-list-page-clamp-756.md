# Agent Note: PlanRun 列表末页缩水钳回（#756）

Status: implemented
Class: bug-fix
Issue: #756

## Decision

`PlanRunListPage` 在 `total` 变化后渲染期将 `page` 钳到 `safePage = min(page, totalPages)`；`showFilteredEmpty` 仅在 `safePage <= totalPages` 时成立，避免越界页误显示筛选空态。

## Alternatives

- `useEffect` 依赖 total 钳页：触发 `set-state-in-effect` lint。
- 后端自动回退 skip：不解决分页条显示 `5 / 4` 的前端矛盾。

## Verification

- `npm run test -- PlanRunListPage.test.tsx`
- `python scripts/run_gates.py check:quick`

## Revisit

- 与 ExpandableDeviceTable（#822）同型；若抽共享 hook 可一并收敛。
