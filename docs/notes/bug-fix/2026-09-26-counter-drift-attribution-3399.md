# counter drift 埋点归因：聚合器批量补齐不记 drift + reconciler 跳 pending run（#3399）

Status: implemented
Class: bug-fix

关联：[#3399](https://github.com/DUElost/stability-test-platform/issues/3399)（本单；owner 裁决
comment `5844037105`）、[#3370](https://github.com/DUElost/stability-test-platform/issues/3370)（唯一验收台账，第 ④ 项）、
[#3359](https://github.com/DUElost/stability-test-platform/pull/3359)（ADR-0052 D1–D5 实施）、
[ADR-0052](../../adr/ADR-0052-terminal-fact-parent-aggregation-decoupling.md) §5 ④ / §8 观测面、
[ADR-0026 §6](../../adr/ADR-0026-plan-execution-scaling.md)（SLO 守卫）。

## Decision

（owner 裁决 **A**，2026-09-26）drift 埋点只保留 **reconciler/补偿路径**，聚合器批量补齐不再记：

1. `recount_plan_run_counters(..., *, record_drift: bool = True)` 增加开关；聚合器
   （`plan_run_finalization._aggregation_round_sync`）以 `record_drift=False` 调用。
   计数重算行为不变——仍写五列、仍返回 `before/after/drifted`。
2. `counter_reconciler` 扫描增加**规格补充**（owner 要求）：`_has_pending_aggregation(db, run_id)`
   为真（该 run 仍有 `plan_run_pending_aggregation` 标记）⇒ **跳过**——不 recount、
   不 commit、不加载 jobs。否则聚合器尚未跑时，reconciler 会把这段正常滞后也记成
   漂移，误报只是换一条路径复发。摘要新增 `skipped_pending` 计数。
3. 告警规则（`StabilityPlanRunCounterDrift`）与指标形态（`label=mode`）**不变**；
   验收 ④「counter drift = 0」的口径 = **reconciler 路径**（聚合器批量补齐不计入）。

## Alternatives

- **B 重标定告警阈值**：否决（owner）——要从指标里剔除聚合器那部分，而这部分
  每轮都有，剔完等于没有，阈值无从标定。
- **C 给指标加 `source` 标签**：否决（owner）——能区分来源，但要同时改指标与规则
  两处，等于在 A 之上再加一层，收益不足。
- **只改聚合器、不做 reconciler 跳过**：否决（owner 规格补充）——聚合器未跑前
  reconciler 仍会误报。
- **给聚合器另设 `..._batch_fill_total` 指标**：不采纳——观测收益无需求支撑，
  且该量本身不该进 SLO 信号（A 的动机即「这不是漂移」）。

## Verification

- `backend/tests/scheduler/test_counter_reconciler_aggregation.py`：既有两条补
  `_has_pending_aggregation → False`；新增 `test_reconcile_skips_run_with_pending_aggregation`
  （`skipped_pending=1`、零 drift 记录、不加载 jobs、不 commit、计数保持不动）。
- `backend/tests/services/test_terminal_aggregation_decoupling_3244.py`：新增
  `test_aggregator_drain_does_not_record_counter_drift`（真实 DB：drain 后
  `terminal/completed` 计数被补齐，五列 drift 指标全不动）。
- `backend/tests/services/test_job_terminalization.py`：新增
  `test_recount_record_drift_false_skips_metric_but_still_recounts`（`drifted` 仍
  返回 True，指标不动）。
- 实跑（worktree `.wt/stp-3399-counter-drift`）：
  - 聚焦四文件 `22 passed in 5.44s`；三条新用例定点 `3 passed, 3732 deselected`；
  - 全量 `backend/tests/` 结果与 `check:quick` 见 PR 描述（逐条贴实际输出）。
- 生产证据（改动前，部署窗实测）：聚合轮次 `aggregation_duration_seconds_count{path=aggregate}`
  == drift terminal 记录数（19 == 19）；本改动部署后该等量关系应失效、告警应回落。

## Revisit

- 部署后按 #3370 六条验收：④ 以 reconciler 路径判定（本口径）；若 reconciler 路径
  仍有 drift，说明真有无标记入口绕开集中服务，届时回到本单讨论。
- 若未来需要「聚合器批量补齐」的可观测性，另立独立指标（不与 SLO 告警共用同一
  信号）并单独评审。
