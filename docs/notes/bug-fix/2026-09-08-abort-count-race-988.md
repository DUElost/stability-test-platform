# 批量 abort 计数按实际 UPDATE 行数，竞态 claim 纳入停止协议

Status: implemented
Class: bug-fix

## Decision

`abort_plan_run` 预读 PENDING 列表后执行带 `status=PENDING` 条件的批量
UPDATE，但原先用 `n = len(pending_ids)` 增加 `aborted_job_count` /
`terminal_job_count`，并把全部预读 id 当作已 ABORTED。Agent 在预读与
UPDATE 之间 claim→RUNNING 时：UPDATE 正确跳过该行，计数器仍虚增；该 Job
又不在 `running_to_signal` 名单中 → 漏发 abort 控制，PlanRun 可能因虚高
终态计数提前 FAILED，而设备上作业仍在跑。

修复（#988）：

- 批量 UPDATE 增加 `.returning(JobInstance.id)`，计数、host 投影、审计
  `count`、`aborted_jobs` 一律按 RETURNING 实际行；
- 预读有、RETURNING 无的 id：`db.refresh` 后若为 RUNNING，并入
  `abort_requested_jobs` / host 控制下发（与原先 RUNNING 路径同协议）；
- 保留 `synchronize_session="fetch"`（见
  [`2026-08-29-abort-batch-orm-sync-contract.md`](./2026-08-29-abort-batch-orm-sync-contract.md)）。

涉及文件：

- `backend/services/plan_run_abort.py`
- `backend/tests/services/test_plan_run_abort_aggregator_race.py`
  （`test_abort_pending_count_uses_returning_after_concurrent_claim`）

## Alternatives

- **预读后对 PENDING 行 `SELECT … FOR UPDATE`**：可消除该竞态，但大批量
  abort（#492 动机）会拉长锁持有、与 claim 路径互相阻塞；放弃。
- **仅用 `rowcount` 修正计数、不补停止协议**：计数正确但 claimed Job
  仍漏 abort 指令，验收第二项不满足；放弃。
- **竞态 id 一律重试 UPDATE**：claim 后已非 PENDING，重试无意义；必须走
  RUNNING 停止协议。

## Verification

- `pytest backend/tests/services/test_plan_run_abort_aggregator_race.py`
- 重点：`test_abort_pending_count_uses_returning_after_concurrent_claim`
  （预读后并发 claim → 计数=1、claimed 进 `abort_requested_jobs`、发
  control、PlanRun 仍 RUNNING）
- `python scripts/run_gates.py check:quick`

## Revisit

若 claim 与 abort 之间还出现 PENDING→非 RUNNING 的第三态（例如直接
FAILED），需扩展 `raced_ids` 分支，不仅处理 RUNNING。
