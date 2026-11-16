# Signal 链接健康条在有待修复项时不再显示正常色（#4012）

Status: implemented
Class: bug-fix

载体 issue：#4012（来源 #3991「前端行为和验收」第 3 条；引入于 #3986）。

## Decision

针对 `frontend/src/components/plan-run/ArchiveStatusCard.tsx` 中「Signal 链接健康」条按取整后百分比（`>= 95%` 为绿）配色、导致 `fixable_link_rate = 0.996` 且 `unlinked_fixable = 1` 时仍显示绿色 `100%` 的问题，按「非零即故障」语义做最小前端修复：

1. **配色判据改用健康状态覆盖（非零选定错误色 `bg-destructive`）**
   - 将 `pctBar` 的第三参数从 `higherIsBetter = false` 收敛为可选 `colorOverride?: string`，保留「HDD 使用率」原有三段阈值（`>= 90` `bg-destructive`、`>= 70` `bg-warning`、`< 70` `bg-success`）不变；
   - 「Signal 链接健康」仅在 `unlinked_fixable === 0 && fixable_link_rate >= 1` 时使用正常色 `bg-success`，一旦 `unlinked_fixable > 0` 或 `fixable_link_rate < 1` 即使用错误色 `bg-destructive`，与同卡片下方 `待修复 N` 的 `text-destructive` 视觉语义保持一致。
2. **百分比取整不再进位为 `100%`**
   - `linkHealthPct(rate, unlinkedFixable)` 在 `rate < 1 || unlinkedFixable > 0` 时将 `Math.round(rate * 100)` 上限夹紧到 `99`（下限 `0`），保证 `0.996`（及四位小数截断为 `1.0` 但仍有待修复项的情况）均不会显示为 `100%`。

## Alternatives

- **非零时使用警示色 `bg-warning` 或沿用 `>= 80` 警示 / `< 80` 错误的分级配色**：后端 `log_observation.py` 与 `signal-link-health-triage` SOP 将 `unlinked_fixable > 0` 定义为真实链接故障，且同一卡片下方 `待修复 N` 已使用 `text-destructive`；若健康条在 `0.996` / `0.8` 时显示黄色而下方文字显示红色，仍会造成同卡状态信号不一致，因此统一选定错误色 `bg-destructive`。
- **继续保留 `higherIsBetter` 分支并仅将阈值改为 `pct === 100`**：无法覆盖 `unlinked_fixable > 0` 且四位小数取整后 `fixable_link_rate` 接近或等于 `1.0` 的边界情况，也会把 `link_stats` 业务规则耦合进通用 `pctBar`。

## Verification

- 修复前在未修改的 `ArchiveStatusCard.tsx` 上执行新增用例，`fixable_link_rate = 0.996` / `unlinked_fixable = 1` 用例为红（收到 `bg-success` 与 `width: 100%`）。
- 修复后执行：
  - `cd frontend && npx vitest run src/components/plan-run/ArchiveStatusCard.test.tsx`：通过（`1 passed, 7 passed`）。
  - `./scripts/project_python.sh scripts/run_gates.py check:quick`：通过（`16 gates` 全绿，其中 `schema-at-head` 因未配置 `DATABASE_URL` 按设计 WARN 跳过；包含 `eslint`、`tsc`、`knip`、`gov-surface`、`ai-work` 等）。

## Revisit

- 若后续运维策略调整为对少量 `unlinked_fixable` 分级告警而非「非零即故障」，可在 `ArchiveStatusCard.tsx` 的 `linkHealthy` / `colorOverride` 判定处统一调整阈值与配色映射。
