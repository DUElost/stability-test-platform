# OpenRouter UI Phase 4：mockups 同步 + 资源页 KPI

Status: implemented
Class: feature

## Decision

收口 #464 四阶段中的 mockups 反哺，并继续资源页稀疏化。

- `docs/design/mockups/plan-execute-v2/styles.css`：色板/圆角/字体/阴影对齐 Phase 1–3（葡萄紫、`.078` border、`--shadow: none`、Jakarta/Geist、radius 0.5rem）；硬编码蓝 `217` 清零。
- Hosts / Devices 顶部筛选 KPI：改稀疏数字卡（`STAT`），去掉 `shadow-sm/md` 选中态。
- Plan 执行页主面板：`shadow-sm` → `shadow-none`。

## Alternatives

- 只改 mockups、不动生产页：mockups 用户看不见，放弃。
- 批量收敛全部 `rounded-xl`：仍留后续，本 PR 不做。

## Verification

```bash
cd frontend && npm run type-check && npm run lint && npx vitest run && npm run build
```

## Revisit

- PlanRun 详情 / 执行页更深层密度。
- #464 方向性收口：若视觉基线已够用，可关 issue 或标剩余为 polish backlog。
