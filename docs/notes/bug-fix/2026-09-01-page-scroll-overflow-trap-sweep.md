# 全站页面滚动：overflow 中间层吞滚轮排查

Status: preview
Class: bug-fix

## 根因

`AppShell` main 为 `overflow-hidden`；页面依赖 `PageContainer overflow-auto` 滚动。中间层若带 `overflow-hidden` 或 `PANEL.root + overflow-x-auto`（y 轴仍 hidden），鼠标在表格/卡片上滚轮时事件被截获，页面看起来「滑不动」。

## 排查结论

| 页面/组件 | 风险 | 处理 |
|-----------|------|------|
| `/audit` | 已修 #699 | `scrollable={false}` + 表格 `flex-1 overflow-auto` |
| `/schedules` | 同 audit（`PANEL + overflow-x-auto`） | 同上 |
| `ExpandableHostTable` / `ExpandableDeviceTable` | 表格外层 `overflow-hidden` | 去掉 hidden，保留圆角边框 |
| `UserTable` | 同上 | 去掉 `overflow-hidden` |
| `RunReportPage` | 多块 `PANEL.root` 长页 | `overflow-visible` 覆盖 |
| 列表页（PlanRun/Plan/脚本/用例/Issue 等） | 仅 `overflow-x-auto`，无 hidden | 低风险，未改 |
| `/notifications` | Card 列表，无 PANEL 表格壳 | 低风险 |
| `PlanExecutePage` / `AssistantPage` | 自管 `overflow-hidden` 分栏 | 有意设计，未改 |
| `PlanRunDetail` 侧栏卡片 | 已在独立滚动区内 | 未改 |

## Verification

```bash
cd frontend && npx tsc --noEmit
cd frontend && npx vitest run src/components/layout/PageContainer.test.tsx
```

目视：`/schedules`、`/hosts`、`/devices`、`/users`、运行报告长页 — 表格/卡片上滚轮可带动页面滚动。

## Revisit

若再出现同类反馈，考虑在 `PANEL` token 分 `root`（裁剪圆角）与 `scrollBody`（可滚动表格外壳）两档，避免页面级手改。
