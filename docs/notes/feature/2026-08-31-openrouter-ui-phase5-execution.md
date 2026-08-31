# OpenRouter UI Phase 5：执行面按页打磨

Status: implemented
Class: feature

## Decision

在 Phase 1–4 tokens/壳/试点页之上，收口执行面残留「软卡片」chrome（#464 按页打磨）。

- PlanRun 详情：`AnomalyDashboard` 去大圆角渐变壳 → `PANEL.root`；`DASHBOARD_SUMMARY_CARD` 圆角收到 `rounded-lg` 且 label 对齐 `STAT`；Hero / KPI / Gate / Stepper / Precheck 去 `shadow-sm`，KPI 用 `STAT`+`KPI_TONE`。
- PlanRun 列表：顶部 3 格 `STAT`（总数 / RUNNING / FAILED，当前页客户端聚合）；行改 `Card` + `hover:bg-muted/30`（保留键盘可点）；Empty 图标缩小。顺带删除唯一引用方消失后的死文件 `clickable-card.tsx`（knip 门禁）。
- Plan 执行：左右轨 / 命令条 / 选择态 / 主舞台 `shadow-none` + `rounded-lg`；`DispatchCockpit` KPI 改 `STAT`。

不改业务逻辑与 API；不扫荡全站 `rounded-xl`；不碰 Assistant 气泡、BulkActionBar、浮层/tooltip、矩阵选中阴影。

## Alternatives

- 全局把 `PANEL.root` 的 `rounded-xl` 改成 `lg`：波及非执行页，放弃。
- PlanRun 列表 STAT 走新聚合 API：过度设计，本批列表本身只拉 50 条。

## Verification

```bash
cd frontend && npm run type-check && npm run lint && npx vitest run && npm run build
```

## Revisit

- 全站硬编码 `rounded-xl/2xl` 收敛。
- #464 可关或标 polish backlog。
