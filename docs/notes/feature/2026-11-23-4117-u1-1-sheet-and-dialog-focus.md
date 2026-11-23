# U1-1：沉淀标准侧滑抽屉 Sheet 与全站受控 Dialog 焦点归位

Status: implemented
Class: feature

## Decision

落实 `#4117` Wave 1 的 U1-1 弹层与抽屉无障碍基座（对应 D2 / `#465` / `#3259` / `#3260`）：

1. **新增标准 `Sheet` 组件**（`frontend/src/components/ui/sheet.tsx`）：基于 `@radix-ui/react-dialog` 封装 `Sheet`、`SheetContent`（`side="right" | "left"`、`size="sm" | "md" | "lg" | "xl"`）、`SheetHeader`、`SheetBody`、`SheetFooter`、`SheetTitle`、`SheetDescription`，统一提供焦点陷阱、Escape 关闭、`aria-modal`、背景锁定与受控关闭焦点归位。
2. **修复全站受控 `Dialog` 关闭焦点回落 `<body>`（#3259）**：在 `frontend/src/components/ui/dialog.tsx` 根组件中增加 `DialogFocusContext`，在受控 `open` 由 `false → true`（及首次 `open=true` 挂载）的 `useLayoutEffect` 阶段捕获当前 `document.activeElement`，关闭时在 `onCloseAutoFocus` 中若原触发元素仍连接于 DOM 则显式归位焦点，一次性覆盖全站 15 处受控弹窗。
3. **收敛现有 3 处手写 `aria-modal` 侧滑抽屉（#3260）**：将 `DeviceDetailDrawer`、`PlanSnapshotDrawer` 与 `AnomalyDashboard` 内的 `PackageRankingDrawer` 迁移至 `ui/sheet.tsx`，移除重复的手写遮罩与 `keydown` 监听，使后续 `U1-3` 静态契约守卫中「手写 `aria-modal` 抽屉」基线直接归零。

## Alternatives

- **在每个业务弹窗逐个加 `Trigger` 或手动传 `triggerRef`**：需要改动 15+ 业务文件且新弹窗仍容易遗漏；在 `Dialog` / `Sheet` 根组件统一捕获并归位焦点是单点治本方案。
- **把 `PackageRankingDrawer` 等 3 个旧抽屉留到 Wave 4 再迁**：会导致新建的 `ui/sheet.tsx` 在 Wave 1 阶段缺少生产消费者（触发 `knip` 闲置文件告警）且推迟关闭 `#3260`；在 U1-1 一并迁移既满足单 PR 内闭环又让守卫白名单直接归零。

## Verification

- Vitest 定向回归：`sheet.test.tsx`、`dialog.test.tsx`、`DeviceDetailDrawer.test.tsx`、`PlanSnapshotDrawer.test.tsx`、`AnomalyDashboard.test.tsx`、`PlanRunDetailPage.test.tsx`；
- 前端类型检查与 lint：`npx tsc --noEmit`、`npx eslint --max-warnings 0`；
- 全量快速门禁：`.venv/bin/python scripts/run_gates.py check:quick`。

## Revisit

- Wave 2 `U2-1b`（`DevicesPage` 设备详情抽屉）与 Wave 3 `U3-1`（`HostsPage` 主机详情抽屉）将直接消费 `Sheet`（`size="lg"`），并在 `stp-ui-review` 实机浏览器验证 Tab 焦点循环与 Escape 焦点归位。
