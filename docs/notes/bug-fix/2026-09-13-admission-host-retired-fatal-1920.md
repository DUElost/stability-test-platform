# 准入泵 fatal 子串清单补 host_retired（#1920）

Status: implemented
Class: bug-fix

来源：2026-09-13 第三轮变更审计（切片二 #1805 交付后的邻域复核）。

## Decision

`admission_pump._FATAL_PUSH_ERROR_SUBSTRINGS` 补 `"host_retired"`：切片二
（`59015659`）给 `push_mismatched_scripts`/`sync_host_via_hot_update` 新增
退役早退返回 `"host_retired"`，但 pump 的 fatal 子串清单未收录 → 落入
transient → 无界 requeue 循环（该类 requeue 不消耗重试预算，且退役主机
agent 仍 ONLINE、sha_mismatch 持续，Phase B 的 HOST_RETIRED fatal 永远
走不到）。补齐后退役主机经 `fail_plan_run_admission` 快速终态化，与其余
切片的「退役 = 确定性拒绝」语义对齐。

## Alternatives

- 在 push_transient 分支特判 host_retired 走 Retryable 但带短 TTL——否：
  退役是持久事实，任何重试都不可能成功，瞬态化只是拖延。
- 只在 Phase B 终检加强——否：Phase A 先抛 Retryable，Phase B 不可达。

## Verification

- `pytest backend/tests/services/test_execution_state_signals_step5a.py -q`：
  TestPushFatalClassification 新增 2 例（裸码 + partial_fail 包裹形态）；
- `python scripts/run_gates.py check:quick`：见 PR 记录。

## Revisit

#1881 同族三单（预算 off-by-one #1921 / 4xx 分级 #1922 / TTL 截断承诺
#1923）已另立；本单仅收口准入泵分类缺口。
