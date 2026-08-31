# OpenRouter UI Phase 2：外壳与通用组件

Status: implemented
Class: feature

## Decision

在 Phase 1 tokens 之上接线外壳，让侧栏/卡片层与内容区可肉眼区分（#464 框架层）。

- `SIDEBAR` 令牌 + `AppShell`/`Sidebar` 使用 `bg-sidebar` / `border-sidebar-border`；nav active/hover 走 `sidebar-accent`（与内容区紫色 `accent` 晕分离）。
- `ELEVATION.sm` 改为 `0 1px 0` 发丝线；新增 `ELEVATION.flat`（`shadow-none`）。`Card` / `PANEL` / `DASHBOARD_SUMMARY_CARD` / `PIPELINE_EDITOR.card` 默认无投影。
- `.card-hover` 取消 `-translate-y` + `shadow-lg`，改为边框强调。
- `destructive` 亮度 `49.8% → 45%`（白字对比约 5.6:1，过 AA）；Button default/destructive 显式 `shadow-none`。Primary 紫底白字测算约 6.3:1，无需改色。

涉及：`tokens.ts`、`Sidebar.tsx`、`AppShell.tsx`、`card.tsx`、`button.tsx`、`index.css`、`colors.ts`。

## Alternatives

- 侧栏仍用 `SURFACE.elevated`（= card）：与内容同色，Phase 1「只换色」观感继续——放弃。
- 批量改 ~59 处硬编码 `rounded-xl`：属 Phase 3 卡片密度收敛，本 PR 不做。
- destructive 改 soft badge（底色 `/10`）：改变实心徽标语义，放弃；只下调 L。

## Verification

```bash
cd frontend && npm run type-check && npm run lint && npx vitest run && npm run build
```

肉眼：侧栏浅灰/近黑与主画布分层；卡片无浮起阴影；危险按钮仍清晰可读。

## Revisit

- Phase 3：Dashboard KPI / 资源页密度与形态。
- Phase 4：mockups `plan-execute-v2/styles.css` 同步。
- 若侧栏与画布对比在浅色下仍偏弱，可把 light `--sidebar` 从 `#fafafa` 微调或加左侧发丝边强调。
