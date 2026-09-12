# abort_plan_run：run_context 分段写（#793 / #827 批次）

Status: implemented
Class: bug-fix

## Decision

复核结论：issue 的两条缺陷中，**缺陷 1（批量记账忽略 UPDATE rowcount）已由
#988 修复**（PR #1092：`RETURNING` 实际行数记账 + `raced_ids` 刷新后按 RUNNING
补发 abort 控制，回归测试 `test_abort_pending_count_uses_returning_after_concurrent_claim`），
本单不再重复。

**缺陷 2（run_context 整段读改写回覆盖并发写者键）仍在**，本单修复：
`abort_plan_run` 原有四处 `pr.run_context = run_ctx; flag_modified(...)`
（早退 QUEUED/PRECHECK 分支、主路径首段 abort_requested、requested_job_ids
刷新、末尾兜底）全部改为库端 **`jsonb_set` 分段更新**
（`_patch_run_context(db, plan_run_id, path, value)`；先例
`dedup_scan.record_scan_archive_state`——归档与 abort/其他状态写者可能同时
更新同一行，整段写回会把对方新增的键抹掉）。末尾不再整段写回，改为按需
patch `precheck` 键 + `db.expire(pr, ["run_context"])`，让同 session 后续读者
（聚合的 `_abort_requested`）读到库端最新值。

实现细节：`COALESCE(NULLIF(run_context, 'null'::jsonb), '{}'::jsonb)`
——SQLAlchemy JSON 列的 `None` 落库是 **JSON `null`**（非 SQL NULL），只用
COALESCE 会触发 `jsonb_set` 的 "cannot set path in scalar"。

## Alternatives

- **取写锁后锁内重读再整写**（issue 给出的另一选项）：改动会触碰 #793 之外
  的 S-1「plan_run 计数锁」根因面（issue 明确「本单只记 abort 专属缺陷」），
  且需连带调整聚合/其他写者；分段 jsonb_set 是既有先例且最小；
- **只改主路径两处、保留早退分支整写**：早退分支同样与归档/其他写者同行写，
  一并分段（改动等量）；
- **缺陷 1 也重写一遍**：否决——#988 已修且有回归测试，重复实现会引入无谓
  diff 与冲突面。

## Verification

实际运行：

- `backend/tests/services/test_plan_run_abort_aggregator_race.py`（含新增
  `test_abort_run_context_patch_preserves_concurrent_writer_keys`）→
  **9 passed**；新增用例是**回归见证**：临时把末尾改回旧整段写 →
  用例变红（`archive` 键被 stale 快照覆盖），恢复分段实现 → 变绿；
- 既有三件套：`test_plan_run_abort.py` + `test_plan_run_abort_aggregator_race.py`
  + `test_plan_run_abort_api.py` → **30 passed**（分段写改造未破坏既有语义，
  含 #988 的 RETURNING/竞态用例）；
- 更广回归：`backend/tests/services`（排除既有失败文件）+ `test_plan_run_abort_api.py` + `test_plans_api.py` → **839 passed, 1 failed**；
  - 失败为**既有问题**（与本单无关，已用干净 main 对照）：
    `test_aggregator_deadlock_regression.py` 4 例在 origin/main 上同样失败；
    `test_postgresql_abort_and_aggregator_are_serialized_by_plan_run_lock`
    在干净 main 上 3/3 失败（本分支 2/3 失败、1/3 通过）；另一执行正在
    `/tmp/stp-baseline` 反复跑该文件排查；
- `ruff check backend/services/plan_run_abort.py` + 测试文件 → All checks passed；
- `python scripts/run_gates.py check:quick` → 7 gates 全绿。

未完成（pending）：

- 真机并发归档 + abort 同 PlanRun 压测（同一行两写者在生产时段叠加）——
  单测已用「读快照后注入外部写」确定性复现并锁住。

## Revisit

- `plan_run_aggregation.maybe_notify_risk_high` 仍是整段写回（持
  `FOR NO KEY UPDATE`）。abort 的分段写使其不再被 abort 覆盖，但**该函数自身**
  仍可能覆盖其它写者的键——属另一触发路径（RISK_HIGH 通知），如现场出现丢键
  再开单对齐 jsonb_set；
- S-1「plan_run 计数锁」（key_share 强度）不在本单范围，仍按原计划跟踪；
- 本单的测试注入技法（同事务内「读快照后写外部键」）可复用到其它
  read-modify-write 竞态的单测。
