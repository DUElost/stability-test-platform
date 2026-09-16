# PlanRun 计数器漂移指标与告警（#77）

Status: implemented
Class: feature

## Decision

按 #77 清单接入「O(1) 计数器漂移」可观测面：

- **指标**：`stability_plan_run_counter_drift_total{plan_run_id, mode}`（mode ∈
  `total`/`terminal`/`completed`/`failed`/`aborted`）。埋点放在
  `recount_plan_run_counters`（`counter_reconciler` 的唯一调用方）——按
  before/after 差异**逐漂移列**打点；mode 经白名单过滤，未知列名不进入 label
  值域（防未来新列悄然扩值域）。
- **告警**：`StabilityPlanRunCounterDrift`（severity=warning，
  `increase(stability_plan_run_counter_drift_total[1h]) > 0`，`for: 5m`）+
  promtool 场景（真实标签形状可触发，注解逐字匹配）。
- **面板**：Grafana「PlanRun Terminalization → Counter Drift (rate/5m) by mode」。
- **文档**：ADR-0026 runbook 新增 §6.2「计数器漂移监控」（指标/告警/面板/SLO/
  处置口径）。

埋点为 **best-effort**：`getattr(run, "id", None)` + helper 内 try/except——
指标侧的缺失/异常永远不得让终态化业务逻辑失败（既有回归
`test_recount_detects_drift` 的 `SimpleNamespace` 无 `id` 曾暴露该点）。

## Alternatives

- **只在 `counter_reconciler` 打点**（不动 `recount`）：漂移列的 before/after
  差异计算就在 `recount` 内——集中在数据处打点保证「漂移判定与打点」同源，
  避免调用方重复计算造成口径漂移。
- **Counter 不带标签**：丢失 mode 维度，面板与排查都需要按列归因（issue 明确
  要求 `mode`）。
- `plan_run_id` 高基数顾虑：理论漂移率 = 0，序列只在真实漂移时产生，可接受。
- **>1% 漂移率 pager 规则**：需要 per-job 终态量作分母指标，本次未实现
  （见 Revisit 与 issue 评论留痕），不发明阈值。

## Verification

- 单测：`test_counter_reconciler_aggregation.py::
  test_reconcile_emits_counter_drift_metric`——mock 漂移 sweep → 仅漂移列
  （terminal/completed）自增、非漂移列不变；
- 暴露面：`test_metrics_counter_drift.py`——`/metrics` scrape 含新指标；
  非白名单 mode 不出现；
- 契约：`tests/test_prometheus_alerts_contract.py`（结构层 + promtool 场景层，
  本机 `promtool` 可用、场景已实跑）+ `test_grafana_dashboard_contract.py`；
- `pytest backend/tests/`、`pytest tests/`、`check:quick` 见 PR 记录。

## Revisit

- SLO pager 规则（漂移率 > 1% 升级）待「per-job 终态量」分母指标补充后落地
  （已在 issue #77 评论留痕）。
- 若未来 `recount_plan_run_counters` 出现调度修复之外的第二调用方，打点应由
  调用方判定漂移语义，避免非漂移场景误计。

## 复核（#2016，2026-09-16）

本文为**决策时点记录**，不改写上文：`stability_plan_run_counter_drift_total` 的 label
已在 #1927 收敛为 `{mode}`（`plan_run_id` 属无界基数，run 归属走日志/审计）。现行口径见
[`adr-0026-admission-and-scale-gray-rollout.md`](../../operations/adr-0026-admission-and-scale-gray-rollout.md)
的「指标」行与 `backend/core/metrics.py`；`tests/test_prometheus_alerts_contract.py` 守该契约。
