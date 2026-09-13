# scan 队列 worker 排空竞态孤立 pending job（#1706）

Status: implemented
Class: bug-fix

## Decision

#1706：`_worker_loop` 在队列排空路径上，原先只在 `finally` 块复位
`_worker_started`。unlock→return→finally 窗口内若 `enqueue_scan_now` 入队并调用
`_ensure_worker`，会因 `_worker_started` 仍为 True 而跳过启动新 worker；旧
worker 随后 return 并复位标志，pending job 被孤立。

**修复**：在排空路径的 `_worker_lock` + `_queue_lock` 嵌套块内、确认
`_pending` 为空后，**释放锁之前**将 `_worker_started = False`。`finally`
块保留作为兜底（#754 异常退出路径）。

## Alternatives

- **仅依赖 finally 复位**：#754 已证明 finally 必要，但正常排空 return 与
  finally 之间仍有窗口，无法消除竞态。
- **enqueue 时强制重启 worker**：改动面更大，且与 `_ensure_worker` 的
  idempotent 语义冲突。
- **在 enqueue 内持 `_worker_lock`**：与 worker 排空路径争锁，易引入死锁或
  更长临界区。

## Verification

- `pytest backend/agent/tests/test_scan_runner_worker_guard.py`：新增
  `test_enqueue_during_drain_exit_does_not_orphan_job`，用 Event 在排空
  return 前注入 enqueue，断言 job 被处理且 pending 清空。

## Revisit

- 若 scan 队列吞吐成为瓶颈，可评估 worker pool；当前单 worker 语义不变。
