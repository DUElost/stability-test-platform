# 远古孤儿「无 detail URI → 重新入队」语义用例（#3459）

Status: implemented
Class: bug-fix

关联：[#3459](https://github.com/DUElost/stability-test-platform/issues/3459)（夜跑新红）、PR #3460（主修复，
[Note](./2026-09-27-phase0-defer-cutoff-fixture-3247.md)）、#3341 / PR #3447（语义来源）、#1175（截止守卫）、#3247（兜底）。

## Decision

回答 #3460 Note 在 Revisit 里留下的开放问题——「没有 detail_uri 的终态 Job 是否也应停止重入队」。
**owner 2026-09-27 裁定：不停止。** 没有 detail URI = 没有待摄入的内容 = 主路径漏发，按 #3341 docstring 的本意照常
重新入队；截止只针对「有 URI 但读不出来（缺失 / 损坏）」。

新增 `test_defer_cutoff_reenqueues_ancient_orphan_without_detail_uri`，与 #3460 修正后的截止用例互为正反例，
**不打桩** `case_result_ingest_pending`，钉住真实的「无 URI」分类：断言它返回 False、recycler 以 `pc:{job_id}` 为键
重新入队恰好一次、不进入截止告警集合。#3447 的两条同类用例都打了桩，这条分类此前没有任何用例覆盖，#3459 正是由此漏过。

## Alternatives

- **改 `recycler.py`，让无 URI 的远古孤儿也截止**（#3459 立单时的建议）：与 #3341 的设计意图相反，会让「主路径漏发、
  本可补偿」的旧 Job 永远得不到后处理。未采用。
- **只保留 #3460 的截止用例**：语义只写在 PR 描述里，将来若有人按 #3459 的原建议改 recycler，不会有任何用例变红。未采用。

## Verification

- 当前 main（含 #3460）上 `TestDeferredPostCompletion` 3 passed。
- 反例：把 recycler 改成「可读截止行也不重入队」，新用例失败（`无 URI 的远古孤儿应重入队一次`），#1175 截止用例仍通过；
  改动已恢复。

## Revisit

已知取舍：「无 URI、且 post_completion 每次都失败」的远古 Job 会在每个回收周期被重新入队（靠 `pc:{job_id}` 同键去重
兜底），#1175 原来的截止能挡住这种情形。若生产上观察到同一 job 的 `deferred_post_completion_enqueued` 反复出现，
再为这类 Job 另设重试上限，而不是恢复一刀切截止。
