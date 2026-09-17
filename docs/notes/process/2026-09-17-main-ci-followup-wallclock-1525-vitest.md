# Main CI 跟进：压墙钟落地 + #1525 前移评估 + vitest 红灯

Status: implemented
Class: process

## Decision

承接
[`2026-09-17-main-ci-wallclock-coverage-audit.md`](./2026-09-17-main-ci-wallclock-coverage-audit.md)
三条顺序项：

### ① 全量墙钟（保核验）

`backend-test` / `frontend-check`：

- 去掉与 PR required **同形**的 `compileall`、`tsc`；
- 夜间关键路径撤下只度量的 `--cov` / vitest `--coverage`（测试集合不变；
  口径见 coverage-measure-only Revisit）；
- `Install pinned promtool` 后 **agent ∥ tests/**（同 #2538 形）；保留
  `Run repo-level tests` step 名 + `PROMTOOL_REQUIRED` + `tests/` 锚点。

### ② #1525 前移评估（本轮结论：**不前移**）

规则：同类夜间**确定性**红灯 ≥2 → 评估前移。

| 类 | 近窗证据 | 裁决 |
|---|---|---|
| `frontend-check` / vitest `matchMedia` | 2 次（`35148237108`、`35172501514`）均在 **#2472 合入前** | **缺陷已修**（#2472，2026-09-17T03:44Z）；不满足「仍复发」→ **不前移** vitest 进 PR |
| `backend-test` / 控制面 | 历史主因仍在；本轮无新的「≥2 次同缺陷仍未修」输入 | **维持夜间全量**；不把 ~255 文件塞进 PR（打破注意力预算）。若后续 backstop 归因出现**同一确定性缺陷 ≥2**，再开专项前移**窄子集** |

### ③ vitest matchMedia 红灯

**已由 #2472 关闭**（`frontend/src/test/setup.ts` 全局 mock）。两夜失败 tip
均不含该合入。本 PR 不重复改前端 setup；下一轮成功 backstop 即闭环验证。

## Alternatives

- **全量删掉 agent / 离线 tests/ 双跑**：核验上 PR 已覆盖，但全量自洽与
  `DATABASE_URL` 注入路径不同；本轮只并行、不去重集合。
- **vitest 前移为 PR required**：否决——修后无复发证据；且会显著冲击注意力预算。
- **保留夜间 cov**：否决——与「只度量却拖 ~19min 关键路径」的 Revisit 出口冲突。

## Verification

```bash
TESTING=1 JWT_SECRET_KEY=ci PYTHONPATH=. python -m pytest \
  tests/test_ci_promtool_scenario_gate.py \
  tests/test_ci_test_db_guard_wiring.py \
  tests/test_offline_subset_guard.py \
  tests/test_agent_env_selfsufficiency.py -q
```

合入后看下一轮 `workflow_dispatch`：`backend-test` 应明显低于 ~19min；
`frontend-check` 不再因 matchMedia unhandled 红（若仍红则另开缺陷单）。

## Revisit

若 cov 数字需要刷新，手动临时加回 `--cov` 跑一轮即可。若 vitest 再出现
≥2 次**不同根因**的确定性红，重新做 #1525 评估（可能信息性 job，而非 required）。
