# PlanRun 死锁回归：`read=True` 实为 FOR SHARE，恢复 FOR NO KEY UPDATE (#1473)

Status: implemented
Class: bug-fix

## Decision

#789（5448cac4）把 4 处 plan_run 行锁从 `with_for_update(key_share=True)` 改为
`with_for_update(read=True)`，前提是「`key_share=True` 渲染 PG FOR KEY SHARE，不互斥
写者」。该前提不成立：SQLAlchemy 2.x 的渲染是

| 调用 | PG 锁模式 |
|---|---|
| `with_for_update()` | FOR UPDATE |
| `with_for_update(key_share=True)` | **FOR NO KEY UPDATE** |
| `with_for_update(read=True)` | **FOR SHARE** |
| `with_for_update(read=True, key_share=True)` | FOR KEY SHARE |

即 `read=True` 把写锁降级为读锁，造成两个后果（均已复现）：

1. **写者不再互斥**：FOR SHARE 与 FOR SHARE 兼容 → 并发终态化重新丢计数；
   同时 `test_no_key_update_still_serializes_writers` 以 `DID NOT RAISE` 失败；
2. **并发终态化死锁**：两事务各持 FOR SHARE 后 UPDATE 计数（S→NX 锁升级）互等，
   PG 报 `DeadlockDetected` —— `test_concurrent_*` 三个用例失败。

修复：4 处生产路径恢复 `key_share=True`（= FOR NO KEY UPDATE，与 FK 触发的
FOR KEY SHARE 兼容 → 无死锁；两个 NX 互斥 → 写者串行化），并同步回退 #789 对
`test_aggregator_deadlock_regression.py` 的期望改写。`counter_reconciler` 计数修平后
补 `apply_plan_run_aggregation_from_counters` 属 #789 的有效部分，保留。

## Alternatives

- 保留 `read=True` 并改写测试为 FOR SHARE 语义 —— 否：放弃写者串行化（丢更新回归）
  且保留 S→NX 升级死锁；
- 改用 `read=True, key_share=True`（FOR KEY SHARE）—— 否：与普通 UPDATE 兼容，
  不互斥任何写者，正是 #789 试图避免的形态；
- 计数改原子 `UPDATE ... RETURNING` —— 可行但属更大重构（#789 note Revisit 已登记），
  本次不扩大范围。

## Verification

- SQL 渲染（仓库 SQLAlchemy 2.0.52 实测编译）：上表四种调用逐条核对；
- `pytest backend/tests/services/test_aggregator_deadlock_regression.py
  backend/tests/services/test_plan_run_abort_aggregator_race.py -q` 连跑 3 次：
  **14 passed ×3**（修复前同命令 3 failed / 11 passed；
  单跑 t1 曾 4 failed 含 DeadlockDetected）；
- `pytest backend/tests/ -q`：**2279 passed, 0 failed**（约 9 分钟）；
- 机制探针（临时脚本，未入库）：两事务各持 FOR SHARE 后并发 UPDATE →
  一方 `DeadlockDetected`、计数 = 1；改用 FOR NO KEY UPDATE 后第二方在 SELECT
  处阻塞至第一方提交（串行化），K 持有者与 NX NOWAIT 兼容。

## Revisit

若计数后续改为原子 `UPDATE ... counter + 1 RETURNING`，可去掉行锁但保留
reconciler 的聚合补触发；届时 `key_share=True` 的注释与本文一并更新。
