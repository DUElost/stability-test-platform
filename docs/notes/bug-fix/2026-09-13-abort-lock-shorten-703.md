# #703 abort 持锁缩短 + DB 池观测

Status: implemented
Class: bug-fix

## Decision

在已合入的前端 refresh 三分（PR #1759）与 abort 推送坍缩（PR #1791）之上，
收口 #703 残留的「长事务占连接」半边：

1. **窄查询**：abort 只 `SELECT id, host_id, status` 于 PENDING/RUNNING，
   不再 `query(JobInstance).all()` 装载含终态在内的整行 ORM（#327 ~497
   RUNNING 时持锁窗口被全表加载拉长）。
2. **分段 commit**：存在 PENDING 时，先 commit `abort_requested` 释放
   `PlanRun FOR NO KEY UPDATE`，再开第二段事务做 #492 批量终态——避免
   批量 UPDATE 期间并发 `complete_job` 在锁上排队并各占一条 QueuePool 连接。
3. **池观测**：`stability_db_pool_checked_out` / `overflow`（sync|async）+
   `stability_plan_run_abort_lock_seconds{phase}`。

终态语义、host control 扇出、汇总 JOB_STATUS 不变。

## Alternatives

- 只调大 `STP_DB_POOL_SIZE`：掩盖写放大，且 #1516 已对齐默认 30/60。
- complete_job 侧 `lock_timeout` 失败快退：改动面更大，且影响非 abort 路径；
  留作 Revisit。
- abort ACK 批量化：需 Agent/协议改动，超出本切片。

## Verification

- `TESTING=1 JWT_SECRET_KEY=test-secret python -m pytest backend/tests/services/test_plan_run_abort_aggregator_race.py -q`
- `python -m pytest backend/tests/test_database_config.py -q`
- `python scripts/run_gates.py check:quick`

## Revisit

- Host control 扇出若上百 host 仍需限流；
- abort ACK 风暴下的 `on_job_terminal` 锁排队仍可能占池——可评估
  `lock_timeout` 或批处理 ACK；
- 与 #1759/#1791 一并现场回归后可评估关闭 #703。
