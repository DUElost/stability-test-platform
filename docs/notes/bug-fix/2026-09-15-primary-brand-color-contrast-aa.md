# 品牌色对比度修复——light `--primary` blue-500 升真 blue-600

Status: implemented
Class: bug-fix

## Decision

上批 GUI 评测修复（`2026-09-14-planrun-logs-page-gui-audit-fixes.md`）留下的 Revisit 项：
light 主题 `--primary: 217 91% 60%`（`#3b82f6`，blue-500）与近白文字的配对实测 **3.68:1**，
低于 WCAG AA 普通文字的 4.5:1（primary 按钮文字 14px 半粗不满足"大字"3:1 豁免线）。
同样配对存在于 `text-primary` 蓝字白底（链接/激活页签/KPI 主色值）。

用户裁决（2026-09-15）采纳加深方案，light 主题三处：

- `--primary` → `221.2 83.2% 53.3%`（**真 blue-600 `#2563eb`**——不是蓝 500 改亮度，
  而是整颗 Tailwind blue-600，避免自造非标准色）；
- `--ring` → 同值（聚焦圈与主色一致）；
- `--sidebar-primary: #3b82f6` → `#2563eb`（light 段）。

实测：白字/primary 按钮 **3.68 → 5.17:1**，蓝字/白底同升至 5.17:1，
`#fafafa` 字/`#2563eb`（侧栏）4.94:1——全部达 AA。

**不动**：dark 主题（其 `--primary` 配深字 5.94:1 本就达标）；dark 段 `--sidebar-primary`
（深底上的蓝色强调，对比度方向相反且达标）；`XTerminal.tsx` 的终端 ANSI 色板与图表
色板（数据系列，无文字配对语义）。

## Alternatives

- `217 91% 48%`（蓝 500 保相降亮度）：自造非标准色，偏离 Tailwind 色阶，弃用。
- 引入 `--primary-strong` 只给实心按钮：品牌色裂成两套，弃用。
- 按钮文字加大到 18.66px 粗体走 3:1 豁免：改变全站按钮形态，弃用。

## Verification

- 对比度脚本（WCAG 相对亮度公式）实测如上；前端 vitest 全量 807 passed、
  `tsc --noEmit`/`eslint` 零输出、`check:quick` 10 gates 全绿。
- 合入后仅前端构建部署即可生效（无后端/迁移/agent 变更），线上以构建 hash 与
  computed style 抽验。

## Revisit

- `CHART_COLORS`（design-system/colors.ts）中的数据系列蓝与品牌蓝目前不同源，
  图表配色属独立设计面，本次未动；若后续要求图表与品牌色对齐再单独立项。
