# Main CI：撤销 agent∥tests/ 并行（tests/ 长尾 + 争用）

Status: implemented
Class: process

## Decision

全量 `backend-test` **不再**把 `backend/agent/tests` 与根 `tests/` 并行；改回串行
（先 agent，再 `tests/`，中间仍先装 promtool）。保留 #2567 的其它压时项：无
重复 compileall、无夜间 `--cov`。

**证据（run 35296277239，含 #2567）**：

| 段 | 耗时 |
|---|---|
| agent（并行步内） | **76.5s** / 2130 passed |
| `tests/`（并行步内） | **502.7s** / 1463 passed |
| 并行步墙钟 | **504s** ≈ max |

本地单独跑 `tests/`（有 promtool、无与 agent 争用）：**1474 passed in 277s**；
慢项几乎全是 `test_prometheus_alerts_contract` 的 promtool 场景 / 逐条阈值漂移
（单例 ~9–19s）。并行时争用把 ~4.5min 拉到 ~8.5min，且长尾已是 `tests/`——
并行无法缩短关键路径，只会加长。

PR 路径 agent∥离线子集（#2538）**保留**：离线子集无 promtool 长尾，并行仍划算。

## Alternatives

- **维持并行、只优化 promtool 用例**：可后续做，但改场景/`for:` 模拟易伤
  #2151/#2236 核验；本轮先停错误编排。
- **全量只跑容器 2 文件 + promtool 文件**：少双跑 PR 已覆盖的离线子集；核验
  上可辩，但缩小夜间「完整根 tests/」语义，本轮不做。

## Verification

```bash
TESTING=1 JWT_SECRET_KEY=ci PYTHONPATH=. python -m pytest \
  tests/test_ci_promtool_scenario_gate.py -q
# Install 仍须先于 Run repo-level tests；consumer 仍含 tests/ + PROMTOOL_REQUIRED
```

合入后下一轮全量：`Run agent tests` 与 `Run repo-level tests` 应重新分列耗时；
二者之和应明显低于并行步的 504s（期望 tests/ 回落到 ~5min 量级而非 ~8.5min）。

## Revisit

若要再压 `tests/`：专项剖 `test_promtool_gate_detects_per_rule_threshold_drift`
是否可在不降「逐条可归因」的前提下合并/加速 promtool 调用；与本编排回退解耦。
