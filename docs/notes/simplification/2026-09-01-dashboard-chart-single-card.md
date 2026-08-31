# 仪表盘：去双层 Card、并掉在线率横条

Status: preview
Class: simplification

## Decision

- 仅被仪表盘外包 `Card` 使用的四个图表（活动 / 设备分布 / 主机负载 / 完成趋势）去掉内层 `border-none` Card，只保留绘图体，消除双层与多余 `p-6`。
- 「主机在线率」整行横条删除；在线率并入「主机总数」KPI suffix：`在线 N · x.x%`。
- 2 列栅格原为奇数（5）留下空位：补 **风险分布**（`GET /results/summary` → `RiskDistributionChart`），与测试结果页同口径。相对主机在线率，这是稳定性主信号。

自带标题卡的排行/通过率趋势图表不动。

## Alternatives considered

- 按测试类型通过/失败：结果页已有，仪表盘重复价值低。
- S/A/B 风险趋势：更强，但需新图组件；先复用现成饼图填空。

## Verification

```bash
cd frontend && npx vitest run src/pages/Dashboard.test.tsx
npm run build && rm -rf dist-preview && mv dist dist-preview
```

## Revisit

若 suffix 过长可改为仅百分比或 tooltip；图表空态高度与 StableResponsiveContainer 若打架再调 `min-h`。若要 S/A/B 趋势再替换本饼图。
