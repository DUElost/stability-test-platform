# UI：色板回退 STP 蓝系，保留布局密度

Status: implemented
Class: feature

## Decision

#464 整站翻新的重点是**布局与元素形态**，不是换色。Phase 1 葡萄紫 / 锌灰画布 / OR destructive 属于色板换皮，与原诉求错位；用户确认原 STP 蓝系无问题。

本 PR **只回退色**：

- `:root` / `.dark` 恢复 Phase 1 前蓝主色 `217`、原 success/warning/destructive、实色 border/input、蓝黑深色画布。
- `CHART_COLORS` 恢复原 6 色板。
- mockups `plan-execute-v2/styles.css` 同步回蓝。

**显式保留**（布局向）：

- `--radius: 0.5rem`、Jakarta / Geist、body `450`
- 侧栏独立 zinc 色阶（分层），仅 `sidebar-primary`/`ring` 改跟蓝 `#3b82f6`
- Phase 2–5 扁平卡 / `STAT` / 执行面密度不动

## Alternatives

- 连侧栏 zinc 一起砍回与 content 同色：会削弱 Phase 2 分层，放弃。
- 连字体/radius 一起回退：与已认可的密度方向冲突，放弃。

## Verification

```bash
cd frontend && npm run type-check && npm run lint && npx vitest run && npm run build
```

## Revisit

后续 #464 polish 只谈布局/元素，不再改主色除非产品明确要求。
