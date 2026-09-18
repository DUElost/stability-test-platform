# Agent Note — 周期回归收缩准入：不可用设备显式剔除并审计（#2651）

Status: implemented
Class: feature

## Decision

`admission_transaction` 终检对 `run_type ∈ {SCHEDULE, CHAIN}` 的 run 启用**收缩
准入**：可重试不可用设备（device_offline / device_error / host_offline /
host_maintenance / active_lease / active_job）从目标集合**显式剔除**——删
`plan_run_target_device` 行、`run_context.admission_excluded_devices` 记录拒因、
`audit_logs` 落 `admission_shrink` 审计行、日志 `admission_shrunk`——run 带余集
继续准入。边界语义：

- fatal 拒因（not_found / no_host / host_retired）与 host 漂移**不进收缩**，
  显式失败语义不变（ADR-0038 D5/D5bis 原语境是退役/迁移类配置发散）；
- 剔除后为空 = 整体暂态（如上一链未收尾全员 busy），维持 `_RetryableAdmission`
  排队而非 fatal——设备恢复后下轮准入照常收缩；
- MANUAL（人工精确圈机）不启用，all-or-nothing 语义完全不变；
- host 投影（plan_run_host）不删行，`total_job_count` 随余集重算（可能为 0）。

生产背景（2026-09-18 run 427）：568 台静态快照下 23-28 台 monkey teardown 后
adb 重连扰动离线，all-or-nothing 终检整链无限退避（~3min/轮，每轮重跑 42 host
脚本校验），reaper 只收 stale-PRECHECK、排队无超时上限。

## Alternatives

1. 给 TaskSchedule 加 `admission_policy` 列（per-schedule 旋钮）——schema 变更
   + UI + 迁移，v1 无实例需求；按 run_type 推导已覆盖「周期/链式=容忍抖动」
   的全部现役场景。Revisit 触发条件：出现要求严格语义的 schedule 实例。
2. 排队超时上限（到点 fatal）——治标：超时值无自然答案，且丢窗仍是丢窗。
3. 窗口前刷新 device_ids——运营动作非平台能力，不解决 CHAIN 后继的冻结快照。

## Verification

- `python -m pytest backend/tests/services/test_admission_queue_step2.py
  test_admission_queue_step3.py test_admission_queue_step4.py
  test_plan_dispatcher.py test_plan_dispatcher_device_validation.py
  test_plan_dispatcher_precheck.py test_plan_run_dispatch_retry.py` →
  172 passed（新增 `TestAdmissionShrink` 4 用例：SCHEDULE/CHAIN 收缩入列、
  离线剔除不阻塞、全不可用保持可重试、fatal 拒因不收缩）。

## Revisit

- 出现需要严格 all-or-nothing 的 schedule 实例时，升级为 per-schedule 旋钮；
- 与 #2648（链触发按 job 终态选设备）协同后，链场景的目标集合语义可整体
  复核一轮。
