---
name: signal-link-health-triage
description: Signal↔DLE 链接健康分诊 SOP（用 fixable_link_rate / unlinked_fixable 判真实链接故障，排除归档及时性假象）。触发时机：链接率异常、unlinked_fixable > 0、巡检发现链接缺口、核对 DEVICE_LOG_FLOW 链接指标。
---

# Signal↔DLE 链接健康分诊

## 执行前置检查

- [ ] 明确读数语义边界（见下）；**不要用 `link_rate` 当失败率**
- [ ] 读数来源：PlanRun watcher-summary（`WatcherSignalLinkStatsOut`，GET 触发计算）
      或 Prometheus `stability_unlinked_fixable_total`

## 标准作业流程（SOP）

1. 读 **`fixable_link_rate` / `unlinked_fixable`**：
   - `fixable_link_rate < 1.0` 或 `unlinked_fixable > 0` → **真实链接故障桶**
     （signal 的 DLE 存在但未链接）→ 继续 2；
2. 归因：`signal_link_reconcile` 调度任务
   （`backend/scheduler/signal_link_reconciler.py` 的 `reconcile_signal_links_once`）
   ——**链接修复的唯一 owner**；查其运行日志与下一轮是否收敛（读路径只读）。
3. 语义排除（避免误报）：
   - `not_yet_archived` 高 = **归档及时性**问题，不是链接故障；
   - `link_rate` 覆盖含未归档事件、天然偏低——**不是失败率**（schema 注释原文）；
   - 三口径拆分依据：
     `docs/notes/feature/2026-08-30-signal-link-stats-three-way-split.md`。

## 后置验证

- `unlinked_fixable` 回归 0（或明确为已知遗留且修复在途）；
- 必要时确认 `stability_unlinked_fixable_total` 不再增长。

## 踩坑守卫（负向约束）

- **不要把 `link_rate` 低 / `not_yet_archived` 高当链接故障上报**——三桶职责不同
  （schema `WatcherSignalLinkStatsOut` 注释 + 三口径 note）；
- 读路径只读：链接修复归 sweep owner（`plan_runs.py` 注释：read-only，repair owned
  by `signal_link_reconcile`），不要在请求路径触发修复；
- `stability_unlinked_fixable_total` 是 GET 触发的**计数**：长时间无请求时计数不增长
  ≠ 无故障，先看 watcher-summary 现值。
