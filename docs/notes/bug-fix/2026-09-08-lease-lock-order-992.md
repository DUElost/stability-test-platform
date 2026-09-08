# complete 与 extend-batch 统一 Job→Lease 锁顺序（#992）

Status: implemented
Class: bug-fix

## Decision

`extend_leases_batch` 在 `_cas_renew_leases`（UPDATE DeviceLease）之前，对仍
可续租的 `JobInstance` 按 `id` 升序 `SELECT … FOR UPDATE`，与
`complete_job`（先锁 Job，再 `release_lease`）同序，消除
complete × extend-batch 死锁环。锁后已非 RUNNING 的候选改标
`job_not_running`。

涉及：`backend/api/routes/agent_api.py`；回归于
`backend/tests/services/test_aggregator_deadlock_regression.py`
（`test_complete_and_extend_batch_same_job_no_deadlock`，需 PostgreSQL）。

## Alternatives

- 改为 Lease→Job（complete 先锁 Lease）：改动 complete / release 面更大，
  且终态路径本已持 Job 锁；放弃。
- 仅依赖 Agent 重试死锁：不消除额外失败与终态延迟；放弃。

## Verification

- `test_complete_and_extend_batch_same_job_no_deadlock`
- 既有 extend-batch 信号用例
  `backend/tests/services/test_execution_state_signals_step5a.py`
- `TESTING=1 JWT_SECRET_KEY=test-secret python -m pytest
  backend/tests/services/test_aggregator_deadlock_regression.py
  backend/tests/services/test_execution_state_signals_step5a.py -q`

## Revisit

若其它路径对同 Job 先碰 Lease 再碰 Job（例如某些 reclaim），需纳入同序约定。
