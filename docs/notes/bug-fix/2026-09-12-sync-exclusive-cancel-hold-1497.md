# SAQ sync exclusive：取消路径互斥提前释放（#1497）

Status: implemented
Class: bug-fix

## Decision

#1497（85d793 F02 / follow-up #1123）：`_run_sync_exclusive` 在
`await asyncio_to_thread(...)` 的协程 `finally` 里 `end(key)`。SAQ 超时/
编排取消只取消协程，**不能停止**已在跑的同步线程；互斥却先于真实工作退出
而释放，同 key 下一轮可与残留副作用重叠（NFS / merge 写盘）。隔离诊断无需
多 worker。

修复：释放权绑定「同步 worker 完成」。

1. `try_begin` 成功后，所有权初始为协程侧（`async`）。
2. worker 入口原子移交为 `worker`；其 `finally` 才 `end(key)`。
3. 协程侧仅在 `BaseException` 且所有权仍为 `async`（worker 从未取得所有权，
   如提交失败）时释放——取消时若 worker 已持有则 no-op，互斥保留到线程结束。

覆盖出口：正常完成 / fn 异常 / 取消（含 worker 已进入）/ 提交前失败不泄漏。

## Alternatives

- 简单删除协程 `finally`、只靠 worker 释放：提交失败路径可能永久占 key；否决。
- 给同步 fn 传 cancel Event：覆盖不了 NFS/工具子进程；#1123 已否决。
- 跨进程 Redis 锁：ADR-0027 多 worker 议题，超出本单最小出口。

## Verification

- `pytest backend/tests/tasks/test_saq_tasks.py -k sync_exclusive -q`
  （含新例 `test_sync_exclusive_cancel_holds_until_worker_done`）
- `python scripts/run_gates.py check:quick`

## Revisit

多副本下进程内 guard 仍不跨进程（#1123 Revisit）；本单只闭合取消交错。
