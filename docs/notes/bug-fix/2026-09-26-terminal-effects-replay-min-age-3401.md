# 副作用重放加时间门槛（#3401 C2-a / #3376 第 1 项）

Status: implemented
Class: bug-fix

## Decision

`counter_reconciler._replay_stale_aggregation_triggers` 的**副作用重放查询**增加
`PlanRun.ended_at < now() - TERMINAL_EFFECTS_REPLAY_MIN_AGE_S`（模块常量 = **120s**，
不新增环境变量）——只推迟恢复，不引入新状态：

- `ended_at` 与 `terminal_effects_state='pending'` 由**同一终态事务**写死
  （`plan_run_aggregation._finalize_plan_run`），该条件精确表达「崩溃窗口是否已过」：
  门槛内 = 正常编排可能仍在执行（不抢跑）；门槛外 = 按恢复通道补偿；
- `NULL ended_at` 不满足 `<` ⇒ 畸形行不重放（fail-quiet）；
- 恢复通道本不参与正常时延预算（ADR-0052 D2），推迟 2 分钟不改变职责；
  超出门槛的罕见重叠仍有下游保护（链 CAS、dedup key、通知幂等、报告刷新幂等）。
- pending 聚合排空通道（`PlanRunPendingAggregation`）**不改**——其语义本就是
  「唤醒丢失即排空」，重复消费幂等。

## Alternatives

- **CAS 抢占（新增 `'replaying'` 中间态）**：否决——重放者崩溃会卡在 `'replaying'`，
  又需一条「卡住的 replaying 回收」路径，正是 D4 用 `pending/done` 两值避免的复杂度；
- **门槛环境变量化**：否决——常量即可，避免把恢复语义暴露成运行期可调面；
- **用 `updated_at`/`created_at` 计时**：否决——`ended_at` 才是终态事务的写入时间，
  与 `pending` 标记同源同刻，判据最直。

## Verification

| 命令 | 结果 |
|---|---|
| `scripts/run_pytest.sh backend/tests/services/test_terminal_aggregation_decoupling_3244.py -q` | **11 passed** |
| 变异（去掉 `ended_at < cutoff` 行） | 新测试 **1 failed**（红点正确）|
| `scripts/run_gates.py check:quick` | **[OK] check:quick（16 gates）** |

新增/更新用例：
- `test_terminal_effects_replay_respects_min_age_gate`（新）：门槛内 pending 不重放
  （`replayed_effects=0`、无通知、状态保持 pending）；越过门槛重放一次并置 `done`；
- `test_reconcile_recovery_replays_effects_once`（改）：崩溃窗口 run 摆到门槛外，
  保留「重放一次、done 拦截不重复」断言。

## Revisit

- **门槛值 120s 回看**：若真实负载下正常副作用块偶发超过 2 分钟，会把「还在跑的
  正常块」误判为崩溃窗口 → 重放虽幂等，仍产生重复通知/链/报告的短窗开销；
  数据来源 = `plan_run_aggregation_replayed_total{kind="terminal_effects"}` 增量 +
  `terminal_effects_replay` 日志密度。
- 若未来引入「副作用块执行时长」观测，可把固定门槛升级为「期望时长 × 系数」。
