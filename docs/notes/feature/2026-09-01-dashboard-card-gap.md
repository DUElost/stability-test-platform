# 仪表盘与多卡页纵向间距

Status: preview
Class: feature

## Decision

仪表盘 KPI / 告警 / 图表区贴死，根因是 `PageContainer` 未挂已有令牌 `LAYOUT.pageGap`（`space-y-6`）。同构审计后，下列多卡/KPI 页一并补齐：

- `Dashboard`
- `ResultsPage`
- `PlanRunListPage`
- `PlanListPage`
- `ProjectDetailPage`
- `WifiPage`
- `TestSuiteDetailPage`
- `RunReportPage`

KPI 栅格 `gap-3` → `gap-4`（仪表盘 / 结果 / Plan 列表 / PlanRun 列表）。

已有 pageGap 或页内自管 `space-y` 的页（项目登记簿、文件服务器、通知、设置等）不动。表格式主舞台（主机/设备）不在本批。

## Verification

```bash
cd frontend && npx tsc --noEmit -p tsconfig.json
npx vitest run src/pages/Dashboard.test.tsx
npm run build && rm -rf dist-preview && mv dist dist-preview
# 预览 :8081 对照正式 :80
```

## Revisit

若表格式页头与表格仍觉挤，再单独加 `pageGap`；勿默认给 bleed 控制台页加全页 `space-y`。
