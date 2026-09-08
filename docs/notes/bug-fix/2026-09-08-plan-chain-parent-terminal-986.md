# 子 Plan prepare 失败不得回滚父 Job 终态（#986）

Status: implemented
Class: bug-fix

## Decision

`on_job_terminal` / `on_job_terminal_sync` 在 PlanRun 聚合成功后、调用
`trigger_next_plan*` 与 dedup enqueue **之前**先 `commit` 父终态事务
（Job 终态、租约释放、计数/聚合结果）。链式 `prepare_plan_run` 失败路径仍会
`session.rollback()`，但此时父事实已落库，rollback 只影响子创建尝试；随后
`_rollback_chain_trigger*` 写入 `chain_dispatch_failed` 并复位
`next_plan_triggered`，由 chain reconciler 补偿重试。

涉及：`backend/services/job_terminalization.py`（抽出
`_post_aggregation_side_effects_{async,sync}`）；契约注释同步于
`plan_chain_trigger.py`。

## Alternatives

- **仅在 `trigger_next_plan` 用 SAVEPOINT 包 prepare**：可防部分同事务回滚，
  但 `prepare`/`complete` 路径内仍有全量 `rollback()`/`commit()`，边界更脆；
  且不解决「父终态与子派发应解耦」的事务语义。
- **把 chain 挪到 `complete_job` 的 commit 之后由调用方显式触发**：改动面更大
  （session_watchdog / lease reconciler / sync aggregator 均需对齐），收益与
  在 terminalization 内 commit-before-trigger 相同。

## Verification

- `test_uncommitted_parent_terminalization_survives_chain_prepare_failure`
  （`backend/tests/services/test_plan_chain_trigger.py`）
- `test_on_job_terminal_sync_bumps_and_aggregates` 断言聚合成功时 `db.commit`
  先于 `trigger_next_plan_sync`
- `TESTING=1 JWT_SECRET_KEY=test-secret python -m pytest
  backend/tests/services/test_plan_chain_trigger.py
  backend/tests/services/test_job_terminalization.py -q`
- `python scripts/run_gates.py check:quick`

## Revisit

若将来要求「子 Run + 父触发标志」与父 Job 终态单事务原子提交，需同时改掉
`trigger_next_plan*` 失败路径的全量 `rollback()`（改 SAVEPOINT 或独立 session），
并重审 Redis dedup enqueue 相对 DB commit 的顺序。
