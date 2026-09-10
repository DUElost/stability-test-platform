# SAQ 超时后的线程残留互斥（#1123）

Status: implemented
Class: bug-fix

## Decision

#1123（R11-F16，设计风险）：SAQ 超时只取消 coroutine；`asyncio.to_thread` 里的
同步工作（merge 的工具子进程 + 汇总写盘、extract 的 NFS 文件拷贝）**无法从外部
终止**，会继续跑完。「task 已取消」不等于「副作用已停止」——重试若与残留线程
同时写同一批目标文件，轻则重复劳动，重则交错写坏产物。

策略 = **互斥**（线程杀不掉，只能不叠上去）：

- `saq_tasks` 新增 `_SyncOverlapGuard`（进程内 per-key busy 集合）与
  `_run_sync_exclusive(key, fn, *args, **fn_kwargs)`：上一轮的残留线程仍在跑时，
  本轮在**协程侧**等待（`asyncio.sleep`，不占线程池、可被 SAQ 正常取消），拿到
  互斥后再进线程执行；等待超预算（`STP_SAQ_SYNC_OVERLAP_WAIT_SECONDS`，默认
  = merge 的 SAQ 超时 `_MERGE_TASK_SAQ_TIMEOUT`，确保预算 ≥ 正常工作量）报
  `saq_sync_overlap_timeout` 浮出，而不是把新一轮副作用叠上去。fn 正常结束或
  抛异常都在 finally 释放互斥。
- 接入点：`merge_task` 的 `run_merge_all_platforms_sync`（key `merge:{plan_run_id}`）
  与 `extract_task` 的 `_run_extract_sync`（key `extract:{plan_run_id}`）—— 两者
  是真正的长跑副作用段。merge 的其余 to_thread（等水位线 / 汇总读）是快速 DB
  操作，不在互斥内。

## Alternatives

- 协作式取消（给同步 fn 传 cancel Event，循环内自查）：只对**自己写的轮询循环**
  有效，NFS 拷贝 / 工具子进程内部无法检查 —— 覆盖不了本单主诉的文件复制；
- 杀线程：Python 没有安全的线程终止（KeyboardInterrupt 注入不可靠且会破坏
  flock/文件句柄状态），不可行；
- 收窄互斥粒度到「每次 to_thread 调用」：merge 的副作用分散在多个 to_thread 与
  协程等待之间，逐调用加锁既碎又漏 —— 按 task 关键段整体互斥。

## Verification

- `pytest backend/tests/tasks/test_saq_tasks.py`：27 passed，新增 4 例 —— 无冲突
  正常执行并释放 / 上轮残留时重试超预算报 `saq_sync_overlap_timeout`（等待期协程
  侧 sleep，可被取消）/ 上轮结束后等待中的重试正常执行（互斥不丢工作）/ fn 抛
  异常也释放互斥（否则后续重试永久阻塞到超预算）；
- `pytest backend/tests/tasks`：1 failed =
  `test_merge_task_mark_timeout_sets_ready_false_despite_pending_zero` —— **干净
  main 基线同样失败**（#1111 改了 `_enqueue_extract_task` 调用签名带
  `scan_round_id`，测试断言未跟上），与本单无关；
- PR #1205 CI：`pr-agent-tests` 曾红——4 个 merge 链测试仍 `patch("asyncio.to_thread")`，
  打不中模块别名 `asyncio_to_thread`（`_run_sync_exclusive` 经此走真实 merge →
  连本机 5432 被拒）。已改为 `monkeypatch.setattr(saq_tasks, "asyncio_to_thread", …)`，
  merge 短路、汇总透传（与同文件 scan 测试一致）；
- `ruff check backend/ tools/ scripts/` 全绿。

## Revisit

- 互斥是**进程内**的：多 worker / 多副本下 key 可能落在不同进程 —— ADR-0027 落地
  后如需跨进程，应升级为 Redis 锁（届时是控制面事实，走 ADR 而非直接上）；
- 等待预算耗尽即报错放弃本轮（而非无限等）：如果运维发现 `saq_sync_overlap_timeout`
  频发，说明正常工作量超过 `_MERGE_TASK_SAQ_TIMEOUT`，应调 SAQ 超时而不是加大
  等待预算；
- `scan_task` 未接入：其副作用在 Agent 侧（ScanRunner 自有 host 级信号量 + staging
  回收），本进程内只有标记与轮询，无共享文件写入。
